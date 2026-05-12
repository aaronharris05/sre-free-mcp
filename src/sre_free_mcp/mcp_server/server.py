"""``sre-mcp-server`` — MCP HTTP/SSE server exposing the SRE tools.

Runs as a Cloud Run service. The Cloud Run job (``sre-runner``) handles
scheduled ticks; this server is for *on-demand* invocations from an AI
agent (Claude Desktop, Cursor, a remote MCP client over SSE, etc.).

Exposes a focused tool set:

  - ``pipeline_health()``                — current red/yellow/green view
  - ``recent_findings(scope, limit)``    — query gap_reports
  - ``list_workflows()``                 — registered workflows
  - ``lookup_workflow(name)``            — one workflow's metadata
  - ``register_workflow(...)``           — upsert into the registry
  - ``run_task(task_name)``              — dispatch to any runner task
  - ``open_incidents()``                 — currently-open incidents
  - ``pending_approvals(action_kind)``   — items needing human review

Tool implementations delegate to the same code paths the runner uses,
so behavior between scheduled and on-demand invocations is identical.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sre_free_mcp.config import Config, apply_retry_overrides, load_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state set at startup
# ---------------------------------------------------------------------------


_CONFIG: Config | None = None
_BQ_CLIENT: Any | None = None


def _ensure_loaded() -> tuple[Config, Any]:
    """Load config + BQ client lazily on first tool invocation.

    Cloud Run scales to zero; cold-start cost is one-time so it's fine
    to defer config load to first request.
    """
    global _CONFIG, _BQ_CLIENT
    if _CONFIG is None:
        config_dir = Path(os.environ.get("SRE_CONFIG_DIR", "/app/config"))
        _CONFIG = load_config(config_dir)
        apply_retry_overrides(_CONFIG)
        logger.info("MCP server loaded config from %s", config_dir)
    if _BQ_CLIENT is None:
        from google.cloud import bigquery  # lazy

        _BQ_CLIENT = bigquery.Client(project=_CONFIG.install.project_id)
    return _CONFIG, _BQ_CLIENT


# ---------------------------------------------------------------------------
# Tools — pure functions invoked by FastMCP decorators in build_server()
# ---------------------------------------------------------------------------


def _pipeline_health() -> dict[str, Any]:
    config, bq = _ensure_loaded()
    sql = f"""
    SELECT
      traffic_light,
      COUNT(*) AS n
    FROM `{config.install.project_id}.{config.install.governance_dataset}.pipeline_health_v1`
    GROUP BY traffic_light
    """
    try:
        rows = {r["traffic_light"]: int(r["n"]) for r in bq.query(sql).result()}
    except Exception:
        logger.exception("pipeline_health query failed")
        return {"error": "pipeline_health query failed"}
    return {"counts": rows}


def _recent_findings(scope: str | None = None, limit: int = 20) -> dict[str, Any]:
    config, bq = _ensure_loaded()
    scope_filter = "AND scope = @scope" if scope else ""
    sql = f"""
    SELECT generated_at, scope, scope_id, gap_kind, severity, details
    FROM `{config.install.project_id}.{config.install.governance_dataset}.gap_reports`
    WHERE resolved_at IS NULL {scope_filter}
    ORDER BY generated_at DESC
    LIMIT @limit
    """
    try:
        from google.cloud import bigquery  # lazy

        params: list[Any] = [
            bigquery.ScalarQueryParameter("limit", "INT64", max(1, min(int(limit), 200)))
        ]
        if scope:
            params.append(bigquery.ScalarQueryParameter("scope", "STRING", scope))
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("recent_findings query failed")
        return {"error": "recent_findings query failed"}
    return {
        "findings": [
            {
                "generated_at": str(r["generated_at"]),
                "scope": r["scope"],
                "scope_id": r["scope_id"],
                "gap_kind": r["gap_kind"],
                "severity": r["severity"],
            }
            for r in rows
        ]
    }


def _list_workflows() -> dict[str, Any]:
    from sre_free_mcp.core.tables import GovernanceTables
    from sre_free_mcp.core.workflows import list_active

    config, bq = _ensure_loaded()
    workflows = list_active(
        bq_client=bq,
        project=config.install.project_id,
        tables=GovernanceTables(dataset=config.install.governance_dataset),
    )
    return {
        "workflows": [
            {
                "name": w.name,
                "cron": w.cron,
                "trigger_kind": w.trigger_kind,
                "idempotent": w.idempotent,
                "owner_team": w.owner_team,
                "status": w.status,
            }
            for w in workflows
        ]
    }


def _lookup_workflow(name: str) -> dict[str, Any]:
    from sre_free_mcp.core.tables import GovernanceTables
    from sre_free_mcp.core.workflows import lookup

    config, bq = _ensure_loaded()
    wf = lookup(
        name,
        bq_client=bq,
        project=config.install.project_id,
        tables=GovernanceTables(dataset=config.install.governance_dataset),
    )
    if wf is None:
        return {"found": False, "name": name}
    return {
        "found": True,
        "workflow": {
            "name": wf.name,
            "cron": wf.cron,
            "trigger_kind": wf.trigger_kind,
            "idempotent": wf.idempotent,
            "owner_team": wf.owner_team,
            "business_purpose": wf.business_purpose,
            "expected_freshness_hours": wf.expected_freshness_hours,
            "expected_cost_per_run_usd": wf.expected_cost_per_run_usd,
            "policy_refs": list(wf.policy_refs),
            "source_path": wf.source_path,
            "status": wf.status,
        },
    }


def _register_workflow(
    name: str,
    *,
    cron: str | None = None,
    trigger_kind: str | None = None,
    idempotent: bool | None = None,
    owner_team: str | None = None,
    business_purpose: str | None = None,
    source_path: str | None = None,
    expected_freshness_hours: int | None = None,
    expected_cost_per_run_usd: float | None = None,
) -> dict[str, Any]:
    from sre_free_mcp.core.tables import GovernanceTables
    from sre_free_mcp.core.workflows import Workflow, register_workflow

    config, bq = _ensure_loaded()
    workflow = Workflow(
        name=name,
        cron=cron,
        trigger_kind=trigger_kind,
        idempotent=idempotent,
        owner_team=owner_team,
        business_purpose=business_purpose,
        source_path=source_path,
        expected_freshness_hours=expected_freshness_hours,
        expected_cost_per_run_usd=expected_cost_per_run_usd,
    )
    register_workflow(
        workflow,
        bq_client=bq,
        project=config.install.project_id,
        tables=GovernanceTables(dataset=config.install.governance_dataset),
    )
    return {"registered": True, "name": name}


def _run_task(task_name: str) -> dict[str, Any]:
    from sre_free_mcp.runner.tasks import TASKS, dispatch

    config, bq = _ensure_loaded()
    if task_name not in TASKS:
        return {"error": f"unknown task {task_name!r}", "available": sorted(TASKS)}
    try:
        result = dispatch(
            task_name, config=config, bq_client=bq, now=datetime.now(UTC)
        )
    except Exception as e:
        logger.exception("run_task %s failed", task_name)
        return {"error": f"{type(e).__name__}: {e}"}
    return {"task": task_name, "result": result}


def _open_incidents() -> dict[str, Any]:
    config, bq = _ensure_loaded()
    sql = f"""
    SELECT id, workflow_name, scope, opened_at, severity, summary
    FROM `{config.install.project_id}.{config.install.governance_dataset}.incidents`
    WHERE closed_at IS NULL
    ORDER BY opened_at DESC
    """
    try:
        rows = list(bq.query(sql).result())
    except Exception:
        logger.exception("open_incidents query failed")
        return {"error": "open_incidents query failed"}
    return {
        "incidents": [
            {
                "id": r["id"],
                "workflow_name": r["workflow_name"],
                "scope": r["scope"],
                "opened_at": str(r["opened_at"]),
                "severity": r["severity"],
                "summary": r["summary"],
            }
            for r in rows
        ]
    }


def _pending_approvals(action_kind: str | None = None) -> dict[str, Any]:
    config, bq = _ensure_loaded()
    extra = "AND action_kind = @action_kind" if action_kind else ""
    sql = f"""
    SELECT id, action_kind, requested_at, narrative
    FROM `{config.install.project_id}.{config.install.governance_dataset}.approval_queue`
    WHERE status = 'pending' {extra}
    ORDER BY requested_at ASC
    """
    try:
        from google.cloud import bigquery  # lazy

        params = []
        if action_kind:
            params.append(
                bigquery.ScalarQueryParameter("action_kind", "STRING", action_kind)
            )
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("pending_approvals query failed")
        return {"error": "pending_approvals query failed"}
    return {
        "approvals": [
            {
                "id": r["id"],
                "action_kind": r["action_kind"],
                "requested_at": str(r["requested_at"]),
                "has_narrative": bool(r["narrative"]),
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


def build_server() -> Any:
    """Construct the FastMCP server with every tool registered.

    Lazy-imports the ``mcp`` package so the rest of sre-free-mcp can
    be used without the MCP SDK installed.

    Binds to ``0.0.0.0:$PORT`` (defaulting to 8080) so the SSE/HTTP
    transport works inside a Cloud Run container. FastMCP's defaults
    are ``127.0.0.1:8000`` which Cloud Run can't reach.
    """
    from mcp.server.fastmcp import FastMCP

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    mcp = FastMCP("sre-free-mcp", host=host, port=port)

    @mcp.tool()
    def pipeline_health() -> dict[str, Any]:
        """Return the current red/yellow/green counts from pipeline_health_v1."""
        return _pipeline_health()

    @mcp.tool()
    def recent_findings(scope: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Return recent unresolved findings from gap_reports.

        Args:
            scope: optional filter (data | cost | freshness | workflow | …).
            limit: max rows to return (capped at 200).
        """
        return _recent_findings(scope=scope, limit=limit)

    @mcp.tool()
    def list_workflows() -> dict[str, Any]:
        """Return every active workflow registered in governance.workflows."""
        return _list_workflows()

    @mcp.tool()
    def lookup_workflow(name: str) -> dict[str, Any]:
        """Return one workflow's full metadata, or ``{found: false}``."""
        return _lookup_workflow(name)

    @mcp.tool()
    def register_workflow(
        name: str,
        cron: str | None = None,
        trigger_kind: str | None = None,
        idempotent: bool | None = None,
        owner_team: str | None = None,
        business_purpose: str | None = None,
        source_path: str | None = None,
        expected_freshness_hours: int | None = None,
        expected_cost_per_run_usd: float | None = None,
    ) -> dict[str, Any]:
        """Upsert a workflow into governance.workflows. Idempotent on name."""
        return _register_workflow(
            name,
            cron=cron,
            trigger_kind=trigger_kind,
            idempotent=idempotent,
            owner_team=owner_team,
            business_purpose=business_purpose,
            source_path=source_path,
            expected_freshness_hours=expected_freshness_hours,
            expected_cost_per_run_usd=expected_cost_per_run_usd,
        )

    @mcp.tool()
    def run_task(task_name: str) -> dict[str, Any]:
        """Dispatch one runner task on demand. See `list_tasks` for names."""
        return _run_task(task_name)

    @mcp.tool()
    def list_tasks() -> dict[str, Any]:
        """List every task name dispatchable via `run_task`."""
        from sre_free_mcp.runner.tasks import TASKS

        return {"tasks": sorted(TASKS.keys())}

    @mcp.tool()
    def open_incidents() -> dict[str, Any]:
        """Return every currently-open incident."""
        return _open_incidents()

    @mcp.tool()
    def pending_approvals(action_kind: str | None = None) -> dict[str, Any]:
        """Return pending approval_queue items, optionally filtered by kind."""
        return _pending_approvals(action_kind=action_kind)

    return mcp


def main() -> None:
    """Entry point for the ``sre-mcp-server`` console script."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s %(message)s",
    )
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    server = build_server()
    if transport == "stdio":
        server.run()
    elif transport in ("sse", "http", "streamable_http"):
        # FastMCP's run() picks SSE when transport='sse'.
        server.run(transport="sse")
    else:
        raise ValueError(f"unknown MCP_TRANSPORT: {transport!r}")


if __name__ == "__main__":
    main()
