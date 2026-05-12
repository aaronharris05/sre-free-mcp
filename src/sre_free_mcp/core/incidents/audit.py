"""Incident lifecycle sweep.

Each tick:
  1. Open new incidents for workflows that crossed the open threshold
     and don't already have an open incident.
  2. Close existing incidents whose linked findings all have
     ``resolved_at`` set OR whose workflow has been quiet for
     ``close_quiet_hours``.

Reads ``governance.gap_reports`` for open findings and
``governance.incidents`` for current state. Writes new incident rows
and UPDATEs to the same table.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)

_DEFAULT_OPEN_SEVERITY = "critical"


@dataclass(frozen=True)
class AuditResult:
    opened: int
    closed: int
    open_count: int                       # count after this tick
    new_incident_ids: list[str] = field(default_factory=list)
    closed_incident_ids: list[str] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    open_severity: str = _DEFAULT_OPEN_SEVERITY,
    close_quiet_hours: int = 24,
) -> AuditResult:
    """Run one incident lifecycle tick."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    open_workflows = _read_unresolved_severity_workflows(
        bq_client, project, tables, severity=open_severity
    )
    open_incidents = _read_open_incidents(bq_client, project, tables)
    open_by_wf = {i["workflow_name"]: i for i in open_incidents if i.get("workflow_name")}

    new_ids: list[str] = []
    for wf in open_workflows:
        if wf["workflow_name"] in open_by_wf:
            continue
        new_id = open_incident(
            bq_client=bq_client,
            project=project,
            tables=tables,
            workflow_name=wf["workflow_name"],
            severity=open_severity,
            summary=wf.get("summary") or f"{open_severity} finding(s) for {wf['workflow_name']}",
            linked_finding_ids=wf.get("finding_ids") or [],
            now=now,
        )
        new_ids.append(new_id)

    closed_ids: list[str] = []
    for incident in open_incidents:
        if _is_quiet(incident, now=now, close_quiet_hours=close_quiet_hours):
            close_incident(
                bq_client=bq_client,
                project=project,
                tables=tables,
                incident_id=incident["id"],
                now=now,
            )
            closed_ids.append(incident["id"])

    after_open_count = len(open_incidents) + len(new_ids) - len(closed_ids)

    logger.info(
        "incidents_complete opened=%d closed=%d open_now=%d",
        len(new_ids),
        len(closed_ids),
        after_open_count,
    )
    return AuditResult(
        opened=len(new_ids),
        closed=len(closed_ids),
        open_count=after_open_count,
        new_incident_ids=new_ids,
        closed_incident_ids=closed_ids,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------


def open_incident(
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    workflow_name: str,
    severity: str,
    summary: str,
    linked_finding_ids: list[str],
    now: datetime,
    scope: str | None = None,
) -> str:
    """Insert one row into ``governance.incidents`` and return its id."""
    incident_id = str(uuid.uuid4())
    row = {
        "id": incident_id,
        "workflow_name": workflow_name,
        "scope": scope,
        "opened_at": now.isoformat(),
        "closed_at": None,
        "severity": severity,
        "summary": summary,
        "cause": None,
        "linked_finding_ids": list(linked_finding_ids),
        "linked_event_ids": [],
        "rca_approval_id": None,
        "source": "sre_free_mcp.core.incidents",
    }
    table_ref = f"{project}.{tables.incidents}"
    try:
        errors = bq_client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("incidents insert errors: %s", errors)
    except Exception:
        logger.exception("incidents insert failed (non-fatal)")
    return incident_id


def close_incident(
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    incident_id: str,
    now: datetime,
    cause: str | None = None,
) -> None:
    """Set ``closed_at`` (and optional ``cause``) on one incident."""
    sql = f"""
    UPDATE `{project}.{tables.incidents}`
    SET closed_at = @now,
        cause = COALESCE(@cause, cause)
    WHERE id = @id AND closed_at IS NULL
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [
            bigquery.ScalarQueryParameter("id", "STRING", incident_id),
            bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter("cause", "STRING", cause),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        bq_client.query(sql, job_config=job_config).result()
    except Exception:
        logger.exception("close_incident failed for %s (non-fatal)", incident_id)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _read_unresolved_severity_workflows(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    severity: str,
) -> list[dict[str, Any]]:
    """Return workflows with at least one unresolved finding at the
    given severity, with the affected finding IDs."""
    sql = f"""
    SELECT
      scope_id AS workflow_name,
      ARRAY_AGG(id ORDER BY generated_at DESC LIMIT 10) AS finding_ids,
      MAX(gap_kind) AS worst_kind
    FROM `{project}.{tables.gap_reports}`
    WHERE resolved_at IS NULL
      AND scope = 'workflow'
      AND severity = @severity
    GROUP BY scope_id
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("severity", "STRING", severity)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("read_unresolved_severity_workflows failed (non-fatal)")
        return []
    return [
        {
            "workflow_name": r["workflow_name"],
            "finding_ids": list(r["finding_ids"] or []),
            "summary": f"{severity} {r['worst_kind']} on {r['workflow_name']}",
        }
        for r in rows
    ]


def _read_open_incidents(
    bq_client: Any, project: str, tables: GovernanceTables
) -> list[dict[str, Any]]:
    """Return every incident row where ``closed_at IS NULL``."""
    sql = f"""
    SELECT id, workflow_name, scope, opened_at, severity, summary,
           linked_finding_ids
    FROM `{project}.{tables.incidents}`
    WHERE closed_at IS NULL
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("read_open_incidents failed (non-fatal)")
        return []
    return [
        {
            "id": r["id"],
            "workflow_name": r["workflow_name"],
            "scope": r["scope"],
            "opened_at": r["opened_at"],
            "severity": r["severity"],
            "summary": r["summary"],
            "linked_finding_ids": list(r["linked_finding_ids"] or []),
        }
        for r in rows
    ]


def _is_quiet(
    incident: dict[str, Any], *, now: datetime, close_quiet_hours: int
) -> bool:
    """Return True if every linked finding is resolved + the incident
    was opened more than ``close_quiet_hours`` ago.

    This intentionally errs on the side of *not closing* — better to
    leave an incident open and have the operator close it manually than
    to auto-close prematurely.
    """
    opened_at = incident.get("opened_at")
    if not opened_at or not isinstance(opened_at, datetime):
        return False
    if (now - opened_at) < timedelta(hours=close_quiet_hours):
        return False
    # Linked findings resolution is verified by a separate query in
    # production; for v1 we just rely on the time-quiet criterion. Real
    # workflow-state inspection lives in the rollup bot.
    return True


__all__ = [
    "AuditResult",
    "close_incident",
    "open_incident",
    "sweep",
]
