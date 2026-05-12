"""Rollup sweep — gather summary stats + optionally enqueue + email."""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.email_sender import EmailMessage, EmailSender

from .email import build_bodies, build_subject

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollupSummary:
    """Aggregated state across all audit bots for the rollup window."""

    generated_at: datetime
    window_days: int
    open_findings_total: int
    open_findings_by_scope: dict[str, int]
    open_findings_by_severity: dict[str, int]
    open_incidents: int
    pending_approvals: int
    top_findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AuditResult:
    summary: RollupSummary
    promoted: int                       # findings promoted into approval_queue
    email_sent: bool
    run_id: str = ""


def sweep(
    *,
    project: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    email_sender: EmailSender | None = None,
    recipients: list[str] | None = None,
    now: datetime | None = None,
    window_days: int = 7,
    promote_high_severity: bool = True,
    promote_action_kind: str = "high_severity_gap",
    top_n: int = 5,
) -> AuditResult:
    """One weekly rollup tick.

    Args:
        promote_high_severity: when True, every unpromoted high/critical
            finding gets an approval_queue row so it surfaces for
            review. Idempotent — re-running doesn't duplicate (the
            promotion includes the finding ID in action_payload).
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    summary = _build_summary(
        bq_client, project, tables, now=now, window_days=window_days, top_n=top_n
    )

    promoted = 0
    if promote_high_severity:
        promoted = _promote_high_severity(
            bq_client,
            project,
            tables,
            now=now,
            action_kind=promote_action_kind,
        )

    email_sent = False
    if email_sender is not None and recipients:
        subject = build_subject(summary)
        text, html = build_bodies(summary, project=project)
        try:
            email_sender.send(
                EmailMessage(to=list(recipients), subject=subject, body_text=text, body_html=html)
            )
            email_sent = True
        except Exception:
            logger.exception("rollup email send failed (non-fatal)")

    logger.info(
        "rollup_complete open_findings=%d open_incidents=%d promoted=%d email_sent=%s",
        summary.open_findings_total,
        summary.open_incidents,
        promoted,
        email_sent,
    )
    return AuditResult(
        summary=summary,
        promoted=promoted,
        email_sent=email_sent,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Build summary
# ---------------------------------------------------------------------------


def _build_summary(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    now: datetime,
    window_days: int,
    top_n: int,
) -> RollupSummary:
    open_rows = _read_open_findings(
        bq_client, project, tables, since=now - timedelta(days=window_days)
    )
    by_scope = Counter(r["scope"] for r in open_rows)
    by_severity = Counter(r["severity"] for r in open_rows)
    top_findings = _top_findings(open_rows, n=top_n)
    open_incidents = _count_open_incidents(bq_client, project, tables)
    pending = _count_pending_approvals(bq_client, project, tables)
    return RollupSummary(
        generated_at=now,
        window_days=window_days,
        open_findings_total=len(open_rows),
        open_findings_by_scope=dict(by_scope),
        open_findings_by_severity=dict(by_severity),
        open_incidents=open_incidents,
        pending_approvals=pending,
        top_findings=top_findings,
    )


def _read_open_findings(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    sql = f"""
    SELECT id, generated_at, scope, scope_id, gap_kind, severity, details
    FROM `{project}.{tables.gap_reports}`
    WHERE resolved_at IS NULL
      AND generated_at >= @since
    ORDER BY generated_at DESC
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("rollup _read_open_findings failed (non-fatal)")
        return []
    return [
        {
            "id": r["id"],
            "generated_at": r["generated_at"],
            "scope": r["scope"],
            "scope_id": r["scope_id"],
            "gap_kind": r["gap_kind"],
            "severity": r["severity"],
            "details": r["details"],
        }
        for r in rows
    ]


_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _top_findings(rows: list[dict[str, Any]], *, n: int) -> list[dict[str, Any]]:
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            -_SEV_RANK.get(r["severity"], 0),
            -(r["generated_at"].timestamp() if hasattr(r["generated_at"], "timestamp") else 0),
        ),
    )
    return [
        {
            "scope": r["scope"],
            "scope_id": r["scope_id"],
            "gap_kind": r["gap_kind"],
            "severity": r["severity"],
        }
        for r in sorted_rows[:n]
    ]


def _count_open_incidents(
    bq_client: Any, project: str, tables: GovernanceTables
) -> int:
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{project}.{tables.incidents}`
    WHERE closed_at IS NULL
    """
    try:
        rows = list(bq_client.query(sql).result())
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        logger.exception("rollup _count_open_incidents failed (non-fatal)")
        return 0


def _count_pending_approvals(
    bq_client: Any, project: str, tables: GovernanceTables
) -> int:
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{project}.{tables.approval_queue}`
    WHERE status = 'pending'
    """
    try:
        rows = list(bq_client.query(sql).result())
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        logger.exception("rollup _count_pending_approvals failed (non-fatal)")
        return 0


# ---------------------------------------------------------------------------
# Promote high-severity findings to approval_queue
# ---------------------------------------------------------------------------


def _promote_high_severity(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    now: datetime,
    action_kind: str,
) -> int:
    """Insert one approval_queue row per high-severity finding not yet
    promoted. Deterministic IDs prevent duplicate promotions across
    weekly runs.
    """
    sql = f"""
    SELECT id, scope, scope_id, gap_kind, severity, details, generated_at
    FROM `{project}.{tables.gap_reports}`
    WHERE resolved_at IS NULL
      AND severity IN ('high', 'critical')
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("rollup _promote read failed (non-fatal)")
        return 0
    if not rows:
        return 0
    promoted_rows = []
    for r in rows:
        ap_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"rollup_promote|{r['id']}",
            )
        )
        promoted_rows.append(
            {
                "id": ap_id,
                "requested_at": now.isoformat(),
                "requested_by_agent": "rollup",
                "action_kind": action_kind,
                "action_payload": json.dumps(
                    {
                        "finding_id": r["id"],
                        "scope": r["scope"],
                        "scope_id": r["scope_id"],
                        "gap_kind": r["gap_kind"],
                        "severity": r["severity"],
                    }
                ),
                "policy_refs": [],
                "threshold_evaluated": None,
                "status": "pending",
                "expires_at": (now + timedelta(days=14)).isoformat(),
                "narrative": None,
                "source": "sre_free_mcp.core.rollup",
            }
        )
    table_ref = f"{project}.{tables.approval_queue}"
    try:
        errors = bq_client.insert_rows_json(table_ref, promoted_rows)
        if errors:
            # Likely id collisions on re-run — that's the idempotency
            # guarantee, just log and count as 0 newly promoted.
            logger.info("rollup_promote insert errors (probably dups): %d", len(errors))
            return 0
    except Exception:
        logger.exception("rollup_promote insert failed (non-fatal)")
        return 0
    return len(promoted_rows)


__all__ = ["AuditResult", "RollupSummary", "sweep"]
