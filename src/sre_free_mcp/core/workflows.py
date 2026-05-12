"""Workflow registry — register, lookup, and list workflows.

Every bot in sre-free-mcp keys off rows in ``governance.workflows``.
This module is the canonical API for reading and writing that table.

* :func:`register_workflow` — INSERT or UPDATE (idempotent on ``name``).
* :func:`lookup` — read one workflow by name.
* :func:`list_active` — iterate active workflows.
* :func:`is_idempotent` — fast yes/no check used by the retry
  orchestrator.

The MCP server wraps :func:`register_workflow` as a tool so customers
can declare workflows from their AI agent without writing SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Workflow:
    """One row from ``governance.workflows``."""

    name: str
    cron: str | None = None
    trigger_kind: str | None = None
    idempotent: bool | None = None
    owner_team: str | None = None
    business_purpose: str | None = None
    expected_freshness_hours: int | None = None
    expected_cost_per_run_usd: float | None = None
    policy_refs: list[str] = field(default_factory=list)
    source_path: str | None = None
    status: str = "active"


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def register_workflow(
    workflow: Workflow,
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
) -> None:
    """Upsert ``workflow`` into ``governance.workflows``.

    Idempotent on ``workflow.name`` — re-registering the same workflow
    updates ``updated_at`` and any fields the caller changed, without
    duplicating the row.
    """
    if not workflow.name:
        raise ValueError("workflow.name is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    table_ref = f"{project}.{tables.workflows}"

    # MERGE so this is one statement per workflow regardless of
    # whether the row already exists. Parameters guard against SQL
    # injection (cron/source_path may come from user-controlled config).
    sql = f"""
    MERGE INTO `{table_ref}` T
    USING (SELECT @name AS name) S
    ON T.name = S.name
    WHEN MATCHED THEN UPDATE SET
      cron = @cron,
      trigger_kind = @trigger_kind,
      idempotent = @idempotent,
      owner_team = @owner_team,
      business_purpose = @business_purpose,
      expected_freshness_hours = @expected_freshness_hours,
      expected_cost_per_run_usd = @expected_cost_per_run_usd,
      policy_refs = @policy_refs,
      source_path = @source_path,
      status = @status,
      updated_at = @now,
      source = 'sre_free_mcp.register_workflow'
    WHEN NOT MATCHED THEN INSERT (
      name, cron, trigger_kind, idempotent, owner_team, business_purpose,
      expected_freshness_hours, expected_cost_per_run_usd, policy_refs,
      source_path, status, created_at, updated_at, source, ingest_at
    ) VALUES (
      @name, @cron, @trigger_kind, @idempotent, @owner_team, @business_purpose,
      @expected_freshness_hours, @expected_cost_per_run_usd, @policy_refs,
      @source_path, @status, @now, @now, 'sre_free_mcp.register_workflow', @now
    )
    """
    from google.cloud import bigquery  # lazy

    params = [
        bigquery.ScalarQueryParameter("name", "STRING", workflow.name),
        bigquery.ScalarQueryParameter("cron", "STRING", workflow.cron),
        bigquery.ScalarQueryParameter("trigger_kind", "STRING", workflow.trigger_kind),
        bigquery.ScalarQueryParameter("idempotent", "BOOL", workflow.idempotent),
        bigquery.ScalarQueryParameter("owner_team", "STRING", workflow.owner_team),
        bigquery.ScalarQueryParameter("business_purpose", "STRING", workflow.business_purpose),
        bigquery.ScalarQueryParameter(
            "expected_freshness_hours", "INT64", workflow.expected_freshness_hours
        ),
        bigquery.ScalarQueryParameter(
            "expected_cost_per_run_usd", "FLOAT64", workflow.expected_cost_per_run_usd
        ),
        bigquery.ArrayQueryParameter("policy_refs", "STRING", workflow.policy_refs),
        bigquery.ScalarQueryParameter("source_path", "STRING", workflow.source_path),
        bigquery.ScalarQueryParameter("status", "STRING", workflow.status),
        bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    bq_client.query(sql, job_config=job_config).result()


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def lookup(
    name: str,
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables | None = None,
) -> Workflow | None:
    """Read one workflow by name. Returns None if absent."""
    tables = tables or GovernanceTables()
    sql = f"""
    SELECT name, cron, trigger_kind, idempotent, owner_team, business_purpose,
           expected_freshness_hours, expected_cost_per_run_usd, policy_refs,
           source_path, status
    FROM `{project}.{tables.workflows}`
    WHERE name = @name
    LIMIT 1
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("name", "STRING", name)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("workflows_lookup_failed name=%s", name)
        return None
    if not rows:
        return None
    r = rows[0]
    return Workflow(
        name=r["name"],
        cron=r["cron"],
        trigger_kind=r["trigger_kind"],
        idempotent=r["idempotent"],
        owner_team=r["owner_team"],
        business_purpose=r["business_purpose"],
        expected_freshness_hours=r["expected_freshness_hours"],
        expected_cost_per_run_usd=r["expected_cost_per_run_usd"],
        policy_refs=list(r["policy_refs"] or []),
        source_path=r["source_path"],
        status=r["status"] or "active",
    )


def list_active(
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables | None = None,
) -> list[Workflow]:
    """Return every active workflow."""
    tables = tables or GovernanceTables()
    sql = f"""
    SELECT name, cron, trigger_kind, idempotent, owner_team, business_purpose,
           expected_freshness_hours, expected_cost_per_run_usd, policy_refs,
           source_path, status
    FROM `{project}.{tables.workflows}`
    WHERE status = 'active' OR status IS NULL
    ORDER BY name
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("workflows_list_active_failed")
        return []
    return [
        Workflow(
            name=r["name"],
            cron=r["cron"],
            trigger_kind=r["trigger_kind"],
            idempotent=r["idempotent"],
            owner_team=r["owner_team"],
            business_purpose=r["business_purpose"],
            expected_freshness_hours=r["expected_freshness_hours"],
            expected_cost_per_run_usd=r["expected_cost_per_run_usd"],
            policy_refs=list(r["policy_refs"] or []),
            source_path=r["source_path"],
            status=r["status"] or "active",
        )
        for r in rows
    ]


def is_idempotent(
    name: str,
    *,
    bq_client: Any,
    project: str,
    tables: GovernanceTables | None = None,
) -> bool:
    """Fast yes/no check used by the retry orchestrator.

    NULL is treated as False — a workflow that hasn't been explicitly
    flagged idempotent is presumed unsafe to auto-retry.
    """
    wf = lookup(name, bq_client=bq_client, project=project, tables=tables)
    return bool(wf and wf.idempotent is True)


__all__ = [
    "Workflow",
    "is_idempotent",
    "list_active",
    "lookup",
    "register_workflow",
]
