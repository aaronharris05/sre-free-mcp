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


def dependency_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """Scan pyproject.toml / requirements.txt files for risky deps.

    Sources come from the ``SRE_DEPENDENCY_SOURCES`` env var as a
    comma-separated path list (relative to ``SRE_CONFIG_DIR``'s parent
    or absolute). v1 — once we add a dependency_targets.yaml this
    moves into the config layer.
    """
    import os

    from sre_free_mcp.core.dependency import sweep

    raw = os.environ.get("SRE_DEPENDENCY_SOURCES", "").strip()
    sources = [s.strip() for s in raw.split(",") if s.strip()] if raw else []
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        sources=sources,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def scm_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """SCM hygiene audit. Skipped if SRE_SCM_REPOS / SRE_SCM_TOKEN_SECRET unset.

    Repos format: ``owner1/repo1/main,owner2/repo2/main`` in
    ``SRE_SCM_REPOS``. Token comes from the Secret Manager secret named
    by ``SRE_SCM_TOKEN_SECRET``.
    """
    import logging
    import os

    from sre_free_mcp.core.scm import GitHubClient, sweep
    from sre_free_mcp.secrets import get_secret

    logger = logging.getLogger(__name__)

    raw_repos = os.environ.get("SRE_SCM_REPOS", "").strip()
    token_secret = os.environ.get("SRE_SCM_TOKEN_SECRET", "").strip()
    if not raw_repos or not token_secret:
        logger.info("scm_audit: no SRE_SCM_REPOS or token configured; skipping")
        return {"repos": 0, "findings": [], "skipped": True}

    repos: list[tuple[str, str, str]] = []
    for entry in raw_repos.split(","):
        parts = [p.strip() for p in entry.split("/") if p.strip()]
        if len(parts) >= 3:
            repos.append((parts[0], parts[1], parts[2]))
        elif len(parts) == 2:
            repos.append((parts[0], parts[1], "main"))

    token = get_secret(token_secret, project=config.install.project_id)
    client = GitHubClient(token=token)
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        github_client=client,
        repos=repos,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def test_coverage_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """Test coverage audit. Sources from SRE_COVERAGE_REPORTS env var."""
    import os

    from sre_free_mcp.core.test_coverage import sweep

    raw = os.environ.get("SRE_COVERAGE_REPORTS", "").strip()
    reports = [s.strip() for s in raw.split(",") if s.strip()] if raw else []
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        coverage_reports=reports,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def secret_iam_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """Secret Manager rotation + project IAM hygiene audit."""
    from sre_free_mcp.core.secret_iam import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def security_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """Mirror Security Command Center findings into gap_reports.

    Skipped if ``organization_id`` not set in install.yaml /
    GCP_ORGANIZATION_ID env.
    """
    from sre_free_mcp.core.security import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        organization_id=config.install.organization_id,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def pii_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """Cloud DLP inspection of configured PII targets."""
    from sre_free_mcp.core.pii import sweep

    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        targets=list(config.pii_targets.targets),
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


def llm_safety_audit(
    *, config: Config, bq_client: Any, now: datetime, **_: Any
) -> dict[str, Any]:
    """LLM drift + adversarial smoke testing. No-op when llm.provider='none'."""
    from sre_free_mcp.core.llm_safety import sweep

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
    # Slices 13–16 — repo / GCP-API / LLM audits.
    "dependency_audit": dependency_audit,
    "scm_audit": scm_audit,
    "test_coverage_audit": test_coverage_audit,
    "secret_iam_audit": secret_iam_audit,
    "security_audit": security_audit,
    "pii_audit": pii_audit,
    "llm_safety_audit": llm_safety_audit,
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
