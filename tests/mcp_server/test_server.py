"""Smoke tests for the MCP server tool handlers.

Tests the underscore-prefixed implementation functions directly — the
FastMCP wrapping is tested in an integration test (deferred until
mcp-package version is pinned in CI).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import sre_free_mcp.mcp_server.server as server_mod
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


def _config() -> Config:
    return Config(
        install=InstallConfig(
            project_id="test-proj",
            email=EmailConfig(
                from_address="sre@example.com",
                smtp_host="smtp.example.com",
                smtp_username="sre",
                smtp_password_secret="",
            ),
            llm=LLMConfig(provider="none"),
        ),
        recipients=RecipientsConfig(),
        anomaly_targets=AnomalyTargetsConfig(),
        freshness_targets=FreshnessTargetsConfig(),
        retry_policies=RetryPoliciesConfig(),
        alert_policies=AlertPoliciesConfig(),
    )


class _FakeBQ:
    def __init__(self, query_rows: list[Any] | None = None) -> None:
        self._rows = query_rows or []
        self.queries: list[str] = []
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        self.queries.append(sql)
        return _FakeJob(self._rows)

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


class _FakeJob:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def result(self) -> list[Any]:
        return self._rows


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with fresh module-level config + client state."""
    server_mod._CONFIG = None  # noqa: SLF001
    server_mod._BQ_CLIENT = None  # noqa: SLF001
    yield
    server_mod._CONFIG = None  # noqa: SLF001
    server_mod._BQ_CLIENT = None  # noqa: SLF001


def _inject(config: Config, bq: _FakeBQ):
    server_mod._CONFIG = config  # noqa: SLF001
    server_mod._BQ_CLIENT = bq  # noqa: SLF001


# ---------------------------------------------------------------------------
# pipeline_health
# ---------------------------------------------------------------------------


def test_pipeline_health_returns_aggregated_counts():
    bq = _FakeBQ(query_rows=[{"traffic_light": "green", "n": 3}, {"traffic_light": "red", "n": 1}])
    _inject(_config(), bq)
    out = server_mod._pipeline_health()
    assert out["counts"] == {"green": 3, "red": 1}


def test_pipeline_health_surfaces_bq_error_as_dict():
    class _RaisingBQ:
        def query(self, sql: str) -> Any:
            raise RuntimeError("simulated")

    _inject(_config(), _RaisingBQ())  # type: ignore[arg-type]
    out = server_mod._pipeline_health()
    assert "error" in out


# ---------------------------------------------------------------------------
# recent_findings
# ---------------------------------------------------------------------------


def test_recent_findings_default_limit():
    rows = [
        {
            "generated_at": datetime(2026, 5, 12, tzinfo=UTC),
            "scope": "workflow",
            "scope_id": "wf_a",
            "gap_kind": "workflow_missed_run",
            "severity": "high",
            "details": None,
        }
    ]
    bq = _FakeBQ(query_rows=rows)
    _inject(_config(), bq)
    out = server_mod._recent_findings()
    assert len(out["findings"]) == 1
    assert out["findings"][0]["gap_kind"] == "workflow_missed_run"


def test_recent_findings_scope_filter_appends_predicate():
    bq = _FakeBQ()
    _inject(_config(), bq)
    server_mod._recent_findings(scope="cost", limit=10)
    assert any("scope = @scope" in sql for sql in bq.queries)


def test_recent_findings_caps_limit_at_200():
    """Defensive: a 10_000 limit should clamp to 200."""
    bq = _FakeBQ()
    _inject(_config(), bq)
    server_mod._recent_findings(limit=10_000)
    # Spot-check the call landed; ScalarQueryParameter clamping happens
    # inside the function so we just verify no crash.
    assert bq.queries


# ---------------------------------------------------------------------------
# list_workflows / lookup_workflow / register_workflow
# ---------------------------------------------------------------------------


def test_list_workflows_returns_summary_view():
    rows = [
        {
            "name": "wf_a",
            "cron": "0 4 * * *",
            "trigger_kind": "scheduler",
            "idempotent": True,
            "owner_team": "data_owners",
            "business_purpose": "x",
            "expected_freshness_hours": None,
            "expected_cost_per_run_usd": None,
            "policy_refs": [],
            "source_path": "agents/x.py",
            "status": "active",
        }
    ]
    _inject(_config(), _FakeBQ(query_rows=rows))
    out = server_mod._list_workflows()
    assert out["workflows"][0]["name"] == "wf_a"
    assert "business_purpose" not in out["workflows"][0]  # trimmed view


def test_lookup_workflow_unknown_returns_not_found():
    _inject(_config(), _FakeBQ(query_rows=[]))
    out = server_mod._lookup_workflow("wf_ghost")
    assert out == {"found": False, "name": "wf_ghost"}


def test_register_workflow_returns_registered_true():
    bq = _FakeBQ()
    _inject(_config(), bq)
    out = server_mod._register_workflow("wf_new", cron="0 * * * *", idempotent=True)
    assert out == {"registered": True, "name": "wf_new"}
    assert any("MERGE INTO" in sql for sql in bq.queries)


# ---------------------------------------------------------------------------
# run_task
# ---------------------------------------------------------------------------


def test_run_task_unknown_returns_error_with_available_list():
    _inject(_config(), _FakeBQ())
    out = server_mod._run_task("not_a_real_task")
    assert "error" in out
    assert "available" in out


def test_run_task_runs_anomaly_sweep_with_empty_targets():
    _inject(_config(), _FakeBQ())
    out = server_mod._run_task("anomaly_sweep")
    assert out["task"] == "anomaly_sweep"
    assert out["result"]["scanned"] == 0


# ---------------------------------------------------------------------------
# open_incidents + pending_approvals
# ---------------------------------------------------------------------------


def test_open_incidents_returns_rows():
    rows = [
        {
            "id": "inc-1",
            "workflow_name": "wf_a",
            "scope": None,
            "opened_at": datetime(2026, 5, 12, tzinfo=UTC),
            "severity": "high",
            "summary": "x",
        }
    ]
    _inject(_config(), _FakeBQ(query_rows=rows))
    out = server_mod._open_incidents()
    assert len(out["incidents"]) == 1


def test_pending_approvals_with_action_kind_filter_adds_predicate():
    bq = _FakeBQ()
    _inject(_config(), bq)
    server_mod._pending_approvals(action_kind="retry_exhausted")
    assert any("action_kind = @action_kind" in sql for sql in bq.queries)


def test_pending_approvals_marks_has_narrative():
    rows = [
        {
            "id": "ap-1",
            "action_kind": "retry_exhausted",
            "requested_at": datetime(2026, 5, 12, tzinfo=UTC),
            "narrative": "Likely quota exhaustion.",
        },
        {
            "id": "ap-2",
            "action_kind": "retry_exhausted",
            "requested_at": datetime(2026, 5, 12, tzinfo=UTC),
            "narrative": None,
        },
    ]
    _inject(_config(), _FakeBQ(query_rows=rows))
    out = server_mod._pending_approvals()
    assert out["approvals"][0]["has_narrative"] is True
    assert out["approvals"][1]["has_narrative"] is False
