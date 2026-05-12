"""Job uptime audit — sweep orchestrator.

Daily entrypoint:

1. Read every active row from ``governance.workflows``.
2. For each, resolve the Cloud Run job (via ``workflow_name`` label)
   and pull its last-N execution statuses from the Cloud Run API.
3. Resolve scheduler pause state from Cloud Scheduler (jobs that
   expect a scheduler).
4. Apply :func:`evaluate` per workflow.
5. Bulk-insert findings into ``gap_reports`` (scope='workflow').

The audit is read-only beyond the gap_reports write. Cloud Run / Cloud
Scheduler reads happen via lazy imports so unit tests that stub
:func:`query_statuses` don't need the GCP client libraries installed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import WorkflowStatus, evaluate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    workflows: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    region: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
    grace_hours: float = 2.0,
    failing_streak: int = 3,
) -> AuditResult:
    """Run one daily job-uptime sweep."""
    if not project:
        raise ValueError("project is required")
    if not region:
        raise ValueError("region is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    statuses = query_statuses(bq_client, project, region, tables=tables, now=now)
    logger.info("job_uptime loaded %d workflows", len(statuses))

    # Resolve scheduler pause state for any workflow expecting a
    # scheduler. Convention: a Cloud Run job ``<job>`` has a Cloud
    # Scheduler trigger ``<job>-trigger`` alongside it.
    expected_triggers = [
        f"{s.job_name}-trigger"
        for s in statuses
        if s.trigger_kind == "scheduler" and s.job_name
    ]
    if expected_triggers:
        pause_state = fetch_scheduler_pause_state(project, region, expected_triggers)
        for s in statuses:
            if s.trigger_kind == "scheduler" and s.job_name:
                trigger_name = f"{s.job_name}-trigger"
                if trigger_name in pause_state:
                    s.scheduler_paused = pause_state[trigger_name]

    findings: list[Finding] = []
    for s in statuses:
        findings.extend(
            evaluate(s, now, grace_hours=grace_hours, failing_streak=failing_streak)
        )

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.job_uptime",
        )

    logger.info(
        "job_uptime_complete workflows=%d findings=%d",
        len(statuses),
        len(findings),
    )

    return AuditResult(
        workflows=len(statuses),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Status loaders — patched in tests
# ---------------------------------------------------------------------------


def query_statuses(
    bq_client: Any,
    project: str,
    region: str,
    *,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
) -> list[WorkflowStatus]:
    """Read every active workflow and its recent execution history."""
    tables = tables or GovernanceTables()
    sql = f"""
    SELECT name, cron, trigger_kind
    FROM `{project}.{tables.workflows}`
    WHERE status = 'active' OR status IS NULL
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("query_statuses: workflows read failed")
        return []

    job_index = _list_jobs_by_workflow_label(project, region)
    statuses: list[WorkflowStatus] = []
    for r in rows:
        wf_name = r["name"]
        job_name = job_index.get(wf_name)
        last_at: datetime | None = None
        last_status: str | None = None
        recent: list[str] = []
        if job_name:
            last_at, last_status, recent = _last_executions(project, region, job_name)
        statuses.append(
            WorkflowStatus(
                name=wf_name,
                cron=r["cron"],
                trigger_kind=r["trigger_kind"],
                job_name=job_name,
                last_execution_at=last_at,
                last_execution_status=last_status,
                last_n_statuses=recent,
                scheduler_paused=None,
            )
        )
    return statuses


def _list_jobs_by_workflow_label(project: str, region: str) -> dict[str, str]:
    """Return ``{workflow_name_label: job_name}`` for every Cloud Run
    job in the region. Convention: every job carries a ``workflow_name``
    label matching its workflows row."""
    out: dict[str, str] = {}
    try:
        from google.cloud import run_v2  # lazy

        client = run_v2.JobsClient()
        parent = f"projects/{project}/locations/{region}"
        for job in client.list_jobs(parent=parent):
            labels = dict(job.labels) if job.labels else {}
            wf = labels.get("workflow_name")
            if wf:
                out[wf] = job.name.split("/")[-1]
    except Exception:
        logger.exception("list_jobs_by_workflow_label failed (non-fatal)")
    return out


def _last_executions(
    project: str, region: str, job_name: str, *, limit: int = 5
) -> tuple[datetime | None, str | None, list[str]]:
    """Return ``(last_completion_time, last_status, last_n_statuses)``."""
    try:
        from google.cloud import run_v2  # lazy

        client = run_v2.ExecutionsClient()
        parent = f"projects/{project}/locations/{region}/jobs/{job_name}"
        executions = list(client.list_executions(parent=parent))
        executions.sort(
            key=lambda e: e.create_time if e.create_time else datetime.min,
            reverse=True,
        )
        statuses: list[str] = []
        last_at: datetime | None = None
        last_status: str | None = None
        for ex in executions[:limit]:
            cond_status = _execution_status(ex)
            if cond_status is None:
                continue
            statuses.append(cond_status)
            if last_at is None and ex.completion_time:
                last_at = ex.completion_time
                last_status = cond_status
        return last_at, last_status, statuses
    except Exception:
        logger.exception("_last_executions failed for %s (non-fatal)", job_name)
        return None, None, []


def _execution_status(execution: Any) -> str | None:
    """Map a Cloud Run execution's ``Completed`` condition to
    ``'ok'`` | ``'failed'`` | ``None`` (running / unknown)."""
    try:
        from google.cloud import run_v2  # lazy
    except Exception:
        return None
    for cond in getattr(execution, "conditions", []) or []:
        if getattr(cond, "type_", "") == "Completed":
            state = getattr(cond, "state", None)
            if state == run_v2.Condition.State.CONDITION_SUCCEEDED:
                return "ok"
            if state == run_v2.Condition.State.CONDITION_FAILED:
                return "failed"
    return None


def fetch_scheduler_pause_state(
    project: str, region: str, names: list[str]
) -> dict[str, bool]:
    """Return ``{trigger_name: is_paused}`` for the requested triggers.
    Triggers that don't exist are absent from the returned dict."""
    try:
        from google.cloud import scheduler_v1  # lazy

        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{project}/locations/{region}"
        out: dict[str, bool] = {}
        for job in client.list_jobs(parent=parent):
            short = job.name.split("/")[-1]
            if short in names:
                out[short] = job.state != scheduler_v1.Job.State.ENABLED
        return out
    except Exception:
        logger.exception("fetch_scheduler_pause_state failed (non-fatal)")
        return {}


__all__ = ["AuditResult", "fetch_scheduler_pause_state", "query_statuses", "sweep"]
