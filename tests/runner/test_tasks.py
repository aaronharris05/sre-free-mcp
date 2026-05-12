"""Unit tests for the runner task dispatch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.config import (
    AlertPoliciesConfig,
    AnomalyTargetsConfig,
    Config,
    EmailConfig,
    FreshnessTargetsConfig,
    InstallConfig,
    LLMConfig,
    RecipientsConfig,
    RetryPoliciesConfig,
)
from sre_free_mcp.runner import tasks

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _config() -> Config:
    return Config(
        install=InstallConfig(
            project_id="test-proj",
            email=EmailConfig(
                from_address="sre@example.com",
                smtp_host="smtp.example.com",
                smtp_username="sre",
                smtp_password_secret="",  # empty → NullEmailSender
            ),
            llm=LLMConfig(provider="none"),
        ),
        retry_policies=RetryPoliciesConfig(),
        recipients=RecipientsConfig(groups={"governance_owners": ["ops@example.com"]}),
        anomaly_targets=AnomalyTargetsConfig(),
        freshness_targets=FreshnessTargetsConfig(),
        alert_policies=AlertPoliciesConfig(),
    )


class _FakeBQ:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        self.queries.append(sql)
        return _FakeJob()

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


class _FakeJob:
    def result(self) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_registry_contains_expected_tasks():
    expected = {
        "install_ddl",
        "retry_tick",
        "anomaly_sweep",
        "freshness_sweep",
        "cost_sweep",
        "job_uptime_sweep",
        "data_catalog_audit",
        "ai_gov_audit",
        "cloud_monitoring_sweep",
        "cloud_monitoring_sync",
        "incidents_tick",
        "rca_tick",
        "rollup",
    }
    assert expected.issubset(set(tasks.TASKS.keys()))


def test_dispatch_unknown_task_raises_with_available_list():
    with pytest.raises(KeyError, match="available"):
        tasks.dispatch("not_a_real_task", config=_config(), bq_client=_FakeBQ(), now=_NOW)


# ---------------------------------------------------------------------------
# Per-task dispatch — exercise each task end-to-end through dispatch()
# ---------------------------------------------------------------------------


def test_install_ddl_runs(monkeypatch):
    monkeypatch.setattr(
        "sre_free_mcp.core.ddl_installer.install",
        lambda *a, **kw: ["01.sql", "02.sql"],
    )
    out = tasks.dispatch("install_ddl", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["applied"] == ["01.sql", "02.sql"]
    assert out["dataset"] == "governance"


def test_retry_tick_runs(monkeypatch):
    bq = _FakeBQ()
    out = tasks.dispatch("retry_tick", config=_config(), bq_client=bq, now=_NOW)
    assert "candidates" in out
    assert "by_outcome" in out


def test_anomaly_sweep_with_empty_targets_no_findings():
    out = tasks.dispatch("anomaly_sweep", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["scanned"] == 0
    assert out["findings"] == []


def test_freshness_sweep_runs():
    out = tasks.dispatch(
        "freshness_sweep", config=_config(), bq_client=_FakeBQ(), now=_NOW
    )
    assert out["scanned"] == 0


def test_cost_sweep_runs():
    out = tasks.dispatch("cost_sweep", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["scanned"] == 0


def test_data_catalog_audit_runs():
    out = tasks.dispatch(
        "data_catalog_audit", config=_config(), bq_client=_FakeBQ(), now=_NOW
    )
    assert out["workflows"] == 0


def test_ai_gov_audit_runs():
    out = tasks.dispatch("ai_gov_audit", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["workflows"] == 0


def test_cloud_monitoring_sweep_runs(monkeypatch):
    monkeypatch.setattr(
        "sre_free_mcp.core.cloud_monitoring.audit.list_open_incidents",
        lambda p: [],
    )
    out = tasks.dispatch(
        "cloud_monitoring_sweep", config=_config(), bq_client=_FakeBQ(), now=_NOW
    )
    assert out["open_incidents"] == 0


def test_cloud_monitoring_sync_runs_with_empty_policies(monkeypatch):
    monkeypatch.setattr(
        "sre_free_mcp.core.cloud_monitoring.audit._list_existing_policies",
        lambda p: {},
    )
    out = tasks.dispatch(
        "cloud_monitoring_sync", config=_config(), bq_client=_FakeBQ(), now=_NOW
    )
    assert out["sync_summary"]["created"] == 0


def test_incidents_tick_runs():
    out = tasks.dispatch(
        "incidents_tick", config=_config(), bq_client=_FakeBQ(), now=_NOW
    )
    assert out["opened"] == 0


def test_rca_tick_runs_with_null_llm():
    """provider='none' in config → NullLLMProvider → skipped_null_llm path."""
    out = tasks.dispatch("rca_tick", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["generated"] == 0


def test_rollup_runs_with_null_email_sender():
    """No smtp_password_secret → NullEmailSender which records but doesn't
    transmit. The sweep itself counts that as email_sent=True since the
    sender returned without raising — the operator can see the message
    in logs."""
    out = tasks.dispatch("rollup", config=_config(), bq_client=_FakeBQ(), now=_NOW)
    assert out["email_sent"] is True
    assert out["summary"]["open_findings_total"] == 0
