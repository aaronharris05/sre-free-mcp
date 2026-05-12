"""Task dispatch for the ``sre-runner`` CLI.

Each task is a thin wrapper that takes a loaded :class:`Config` +
``bq_client`` + ``now``, calls the relevant bot's ``sweep()``
function, and returns a JSON-serializable result dict.

Adding a new task: write a function with signature
``def my_task(*, config, bq_client, now, **kw) -> dict[str, Any]``,
register it in :data:`TASKS`.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any, Callable

from sre_free_mcp.config import Config
from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.email_sender import EmailSender, default_sender
from sre_free_mcp.llm import LLMProvider, default_provider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tables(config: Config) -> GovernanceTables:
    return GovernanceTables(dataset=config.install.governance_dataset)


def _to_dict(result: Any) -> dict[str, Any]:
    """Coerce a bot's frozen dataclass result into a JSON-friendly dict."""
    if dataclasses.is_dataclass(result):
        d = dataclasses.asdict(result)
    elif isinstance(result, dict):
        d = dict(result)
    else:
        d = {"result": str(result)}
    # Convert any Finding lists into trimmed dicts so logs stay readable.
    for key, value in list(d.items()):
        if isinstance(value, list) and value and dataclasses.is_dataclass(value[0]):
            d[key] = [dataclasses.asdict(v) for v in value]
    return d


# ---------------------------------------------------------------------------
# Schema install (special — runs once at deploy time)
# ---------------------------------------------------------------------------


def install_ddl(*, config: Config, bq_client: Any, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.ddl_installer import install

    applied = install(
        project=config.install.project_id, bq_client=bq_client, tables=_tables(config)
    )
    return {"applied": applied, "dataset": config.install.governance_dataset}


# ---------------------------------------------------------------------------
# Retry + audit ticks
# ---------------------------------------------------------------------------


def retry_tick(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.retry.orchestrator import tick

    result = tick(
        project=config.install.project_id,
        region=config.install.region,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def anomaly_sweep(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.anomaly import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        targets=list(config.anomaly_targets.targets),
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def freshness_sweep(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    from sre_free_mcp.core.freshness import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        targets=list(config.freshness_targets.targets),
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def cost_sweep(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.cost import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def job_uptime_sweep(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    from sre_free_mcp.core.job_uptime import sweep

    result = sweep(
        project=config.install.project_id,
        region=config.install.region,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def data_catalog_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    from sre_free_mcp.core.data_catalog import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def ai_gov_audit(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.ai_gov import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def cloud_monitoring_sweep(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    from sre_free_mcp.core.cloud_monitoring import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def cloud_monitoring_sync(*, config: Config, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.cloud_monitoring import sync_policies

    summary = sync_policies(
        project=config.install.project_id,
        policies=list(config.alert_policies.policies),
    )
    return {"sync_summary": summary}


def incidents_tick(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    from sre_free_mcp.core.incidents import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def rca_tick(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.rca import sweep

    llm: LLMProvider = default_provider(
        config.install.llm, project=config.install.project_id
    )
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        llm=llm,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def rollup(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.rollup import sweep

    sender: EmailSender = default_sender(
        config.install.email, project=config.install.project_id
    )
    recipients = (
        config.recipients.lookup("governance_owners")
        if "governance_owners" in config.recipients.groups
        else None
    )
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        email_sender=sender,
        recipients=recipients,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


TaskFn = Callable[..., dict[str, Any]]

TASKS: dict[str, TaskFn] = {
    "install_ddl": install_ddl,
    "retry_tick": retry_tick,
    "anomaly_sweep": anomaly_sweep,
    "freshness_sweep": freshness_sweep,
    "cost_sweep": cost_sweep,
    "job_uptime_sweep": job_uptime_sweep,
    "data_catalog_audit": data_catalog_audit,
    "ai_gov_audit": ai_gov_audit,
    "cloud_monitoring_sweep": cloud_monitoring_sweep,
    "cloud_monitoring_sync": cloud_monitoring_sync,
    "incidents_tick": incidents_tick,
    "rca_tick": rca_tick,
    "rollup": rollup,
}


def dispatch(
    task_name: str,
    *,
    config: Config,
    bq_client: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one task. Raises KeyError if unknown."""
    fn = TASKS.get(task_name)
    if fn is None:
        available = ", ".join(sorted(TASKS))
        raise KeyError(f"unknown task {task_name!r}; available: {available}")
    return fn(config=config, bq_client=bq_client, now=now or datetime.now(UTC))


__all__ = ["TASKS", "dispatch"]
