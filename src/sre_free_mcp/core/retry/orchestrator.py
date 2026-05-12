"""Retry orchestrator — one tick of the retry sweep.

For each red-traffic-light workflow whose worst gap_kind looks
transient:

1. **Veto** if the workflow is non-idempotent (policy + workflows.idempotent).
2. **Veto** if the circuit breaker is open.
3. **Wait** if the last attempt is within the backoff window.
4. **Schedule** via the Cloud Run Admin API.
5. **Exhaust + enqueue RCA** after ``max_attempts`` without success.

Every attempt + outcome lands in ``{dataset}.events`` for breaker
accounting and audit trail.

The orchestrator is *idempotent over a tick*: a fresh tick while a
prior retry is still running counts the in-flight attempt and waits
rather than stacking executions. Multiple ticks during the backoff
window are no-ops by design.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables

from .breaker import state_for as breaker_state
from .policy import lookup as lookup_policy
from .policy import next_backoff_seconds

logger = logging.getLogger(__name__)

# Transient-failure heuristic — gap_kinds the orchestrator considers
# worth a retry attempt before declaring exhausted. Non-transient
# failures (validation, config) skip straight to RCA.
_TRANSIENT_GAP_KINDS: tuple[str, ...] = (
    "workflow_missed_run",
    "workflow_failing_persistently",
)


@dataclass(frozen=True)
class TickResult:
    """Structured result of one orchestrator tick.

    Attributes:
        candidates: number of red workflows evaluated.
        by_outcome: count of decisions keyed by outcome string.
        decisions: per-workflow decision dicts (truncated to 20 in logs
            but full list available here).
        run_id: UUID4 for this tick — useful for log correlation.
        generated_at: tick timestamp.
    """

    candidates: int
    by_outcome: dict[str, int]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def tick(
    *,
    project: str,
    region: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
) -> TickResult:
    """Run one orchestrator tick. Always returns a result — per-workflow
    exceptions are caught + logged, never abort the sweep."""
    if not project:
        raise ValueError("project is required")
    if not region:
        raise ValueError("region is required")

    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    candidates = _read_red_pipelines(bq_client, project, tables)
    decisions: list[dict[str, Any]] = []

    for cand in candidates:
        try:
            decision = _process_one(
                cand,
                bq_client=bq_client,
                project=project,
                region=region,
                tables=tables,
                now=now,
            )
        except Exception as e:
            logger.exception("[%s] retry decision raised", cand.get("workflow_name"))
            decision = {
                "workflow_name": cand.get("workflow_name"),
                "outcome": "error",
                "reason": f"{type(e).__name__}: {e}",
            }
        decisions.append(decision)

    by_outcome: dict[str, int] = {}
    for d in decisions:
        outcome = d.get("outcome", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    result = TickResult(
        candidates=len(decisions),
        by_outcome=by_outcome,
        decisions=decisions,
        run_id=run_id,
        generated_at=now,
    )

    logger.info(
        json.dumps(
            {
                "event": "sre_retry_tick",
                "run_id": run_id,
                "generated_at": now.isoformat(),
                "totals": {**by_outcome, "candidates": len(decisions)},
                "decisions": decisions[:20],
            },
            default=str,
        )
    )

    return result


# ---------------------------------------------------------------------------
# Per-workflow decision
# ---------------------------------------------------------------------------


def _process_one(
    candidate: dict[str, Any],
    *,
    bq_client: Any,
    project: str,
    region: str,
    tables: GovernanceTables,
    now: datetime,
) -> dict[str, Any]:
    """Apply policy + breaker + backoff to one red workflow.

    Always writes one event row regardless of decision so the breaker
    counts every attempt-or-veto consistently.
    """
    wf_name = candidate["workflow_name"]
    policy = lookup_policy(wf_name)

    idempotent = _read_idempotent(bq_client, project, wf_name, tables)
    if policy.require_idempotent and not idempotent:
        _emit_event(
            bq_client,
            project,
            tables=tables,
            workflow_name=wf_name,
            attempt_number=0,
            backoff_used=0,
            outcome="not_idempotent",
            now=now,
        )
        return {"workflow_name": wf_name, "outcome": "not_idempotent"}

    bstate = breaker_state(
        wf_name,
        bq_client=bq_client,
        project=project,
        now=now,
        policy=policy,
        events_table=tables.events,
    )
    if bstate == "open":
        _emit_event(
            bq_client,
            project,
            tables=tables,
            workflow_name=wf_name,
            attempt_number=0,
            backoff_used=0,
            outcome="breaker_open",
            now=now,
        )
        return {"workflow_name": wf_name, "outcome": "breaker_open"}

    prior, last_attempt_at = _read_attempts(bq_client, project, wf_name, tables)
    backoff = next_backoff_seconds(policy, prior_attempts=prior + 1)
    if backoff is None:
        # Already at max_attempts — exhaust + enqueue RCA item.
        _emit_event(
            bq_client,
            project,
            tables=tables,
            workflow_name=wf_name,
            attempt_number=prior + 1,
            backoff_used=0,
            outcome="exhausted",
            now=now,
        )
        _enqueue_rca(bq_client, project, tables=tables, workflow_name=wf_name, now=now, attempts=prior)
        return {"workflow_name": wf_name, "outcome": "exhausted", "attempts": prior}

    if last_attempt_at is not None:
        elapsed = (now - last_attempt_at).total_seconds()
        if elapsed < backoff:
            return {
                "workflow_name": wf_name,
                "outcome": "waiting",
                "wait_remaining_s": int(backoff - elapsed),
            }

    job_name = candidate.get("worst_gap_kind_job") or _resolve_job_name(project, region, wf_name)
    if not job_name:
        _emit_event(
            bq_client,
            project,
            tables=tables,
            workflow_name=wf_name,
            attempt_number=prior + 1,
            backoff_used=backoff,
            outcome="no_job",
            now=now,
        )
        return {"workflow_name": wf_name, "outcome": "no_job"}

    triggered = _trigger_execution(project, region, job_name)
    outcome = "ok" if triggered else "trigger_failed"
    _emit_event(
        bq_client,
        project,
        tables=tables,
        workflow_name=wf_name,
        attempt_number=prior + 1,
        backoff_used=backoff,
        outcome=outcome,
        now=now,
    )
    return {
        "workflow_name": wf_name,
        "outcome": outcome,
        "attempt_number": prior + 1,
        "backoff_used": backoff,
        "breaker": bstate,
    }


# ---------------------------------------------------------------------------
# I/O helpers — patched in tests
# ---------------------------------------------------------------------------


def _read_red_pipelines(
    bq_client: Any, project: str, tables: GovernanceTables
) -> list[dict[str, Any]]:
    """Pull red rows from the pipeline_health_v1 view, filtered to
    transient-pattern gap_kinds."""
    in_list = ", ".join(f"'{g}'" for g in _TRANSIENT_GAP_KINDS)
    sql = f"""
    SELECT
      workflow_name, status, trigger_kind, cron,
      open_findings, worst_severity, worst_gap_kind, worst_finding_at
    FROM `{project}.{tables.pipeline_health}`
    WHERE traffic_light = 'red'
      AND worst_gap_kind IN ({in_list})
    """
    try:
        return [dict(r) for r in bq_client.query(sql).result()]
    except Exception:
        logger.exception("Could not read red pipelines (non-fatal)")
        return []


def _read_idempotent(
    bq_client: Any, project: str, workflow_name: str, tables: GovernanceTables
) -> bool:
    """Return True iff workflows.idempotent is explicitly TRUE."""
    sql = f"""
    SELECT idempotent
    FROM `{project}.{tables.workflows}`
    WHERE name = @name
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("name", "STRING", workflow_name)]
        rows = list(
            bq_client.query(
                sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
            ).result()
        )
        if rows and rows[0]["idempotent"] is True:
            return True
    except Exception:
        logger.exception("Could not read idempotent flag for %s (non-fatal)", workflow_name)
    return False


def _read_attempts(
    bq_client: Any, project: str, workflow_name: str, tables: GovernanceTables
) -> tuple[int, datetime | None]:
    """Return (count_of_prior_attempts_in_window, last_attempt_at)."""
    sql = f"""
    SELECT
      COUNT(*) AS n,
      MAX(occurred_at) AS last_at
    FROM `{project}.{tables.events}`
    WHERE event_kind = 'sre_retry_attempt'
      AND JSON_VALUE(payload, '$.workflow_name') = @name
      AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
      AND JSON_VALUE(payload, '$.outcome') IN ('ok', 'trigger_failed')
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("name", "STRING", workflow_name)]
        rows = list(
            bq_client.query(
                sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
            ).result()
        )
        if rows:
            return int(rows[0]["n"]), rows[0]["last_at"]
    except Exception:
        logger.exception("Could not read attempts for %s (non-fatal)", workflow_name)
    return 0, None


def _resolve_job_name(project: str, region: str, workflow_name: str) -> str | None:
    """Resolve workflow → Cloud Run job name via the ``workflow_name`` label."""
    try:
        from google.cloud import run_v2  # lazy

        client = run_v2.JobsClient()
        parent = f"projects/{project}/locations/{region}"
        for job in client.list_jobs(parent=parent):
            labels = dict(job.labels) if job.labels else {}
            if labels.get("workflow_name") == workflow_name:
                return job.name.split("/")[-1]
    except Exception:
        logger.exception("Could not resolve job name for %s (non-fatal)", workflow_name)
    return None


def _trigger_execution(project: str, region: str, job_name: str) -> bool:
    """Run a Cloud Run job execution. Returns True on accepted (the LRO
    doesn't have to complete; we only need the schedule to land)."""
    try:
        from google.cloud import run_v2  # lazy

        client = run_v2.JobsClient()
        name = f"projects/{project}/locations/{region}/jobs/{job_name}"
        client.run_job(name=name)
        return True
    except Exception:
        logger.exception("run_job failed for %s (non-fatal)", job_name)
        return False


def _emit_event(
    bq_client: Any,
    project: str,
    *,
    tables: GovernanceTables,
    workflow_name: str,
    attempt_number: int,
    backoff_used: int,
    outcome: str,
    now: datetime,
) -> None:
    """Append one row to ``events`` for breaker accounting + audit. A
    failure here logs but never aborts the tick — the next tick will
    catch up via fresh state."""
    table_ref = f"{project}.{tables.events}"
    row = {
        "id": str(uuid.uuid4()),
        "occurred_at": now.isoformat(),
        "agent_id": "sre",
        "trace_id": f"sre_retry:{workflow_name}",
        "event_kind": "sre_retry_attempt",
        "payload": json.dumps(
            {
                "workflow_name": workflow_name,
                "attempt_number": attempt_number,
                "backoff_seconds_used": backoff_used,
                "outcome": outcome,
            }
        ),
        "status": "ok" if outcome in ("ok", "exhausted") else "blocked",
    }
    try:
        errors = bq_client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("events insert errors: %s", errors)
    except Exception:
        logger.exception("Could not write retry event (non-fatal)")


def _enqueue_rca(
    bq_client: Any,
    project: str,
    *,
    tables: GovernanceTables,
    workflow_name: str,
    now: datetime,
    attempts: int,
) -> None:
    """Insert an approval_queue row that an RCA bot will pick up.

    ``action_kind='retry_exhausted'`` is the contract.
    """
    table_ref = f"{project}.{tables.approval_queue}"
    expires = now + timedelta(days=7)
    row = {
        "id": str(uuid.uuid4()),
        "requested_at": now.isoformat(),
        "requested_by_agent": "sre",
        "action_kind": "retry_exhausted",
        "action_payload": json.dumps({"workflow_name": workflow_name, "attempts": attempts}),
        "threshold_evaluated": json.dumps({"max_attempts": attempts, "outcome": "all_failed"}),
        "status": "pending",
        "expires_at": expires.isoformat(),
    }
    try:
        errors = bq_client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("approval_queue insert errors: %s", errors)
    except Exception:
        logger.exception("Could not enqueue RCA item (non-fatal)")


__all__ = ["TickResult", "tick"]
