"""Unit tests for the workflow registry helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.core.workflows import (
    Workflow,
    is_idempotent,
    list_active,
    lookup,
    register_workflow,
)

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    """Captures the SQL + params; returns canned rows for read calls."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.queries: list[tuple[str, list[Any]]] = []
        self._rows = rows or []

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        params = list(getattr(job_config, "query_parameters", None) or [])
        self.queries.append((sql, params))
        return _FakeJob(self._rows)


class _FakeJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


def _wf(**overrides: Any) -> Workflow:
    defaults: dict[str, Any] = {
        "name": "wf_demo",
        "cron": "0 * * * *",
        "trigger_kind": "scheduler",
        "idempotent": True,
        "owner_team": "data_owners",
        "business_purpose": "Sample workflow for tests.",
        "expected_freshness_hours": 6,
        "expected_cost_per_run_usd": 0.10,
        "policy_refs": ["policy-01-2.4"],
        "source_path": "agents/example.py",
        "status": "active",
    }
    defaults.update(overrides)
    return Workflow(**defaults)


# ---------------------------------------------------------------------------
# register_workflow
# ---------------------------------------------------------------------------


def test_register_workflow_emits_merge_sql():
    bq = _FakeBQ()
    register_workflow(_wf(), bq_client=bq, project="my-proj", now=_NOW)
    assert len(bq.queries) == 1
    sql, _ = bq.queries[0]
    assert "MERGE INTO" in sql
    assert "`my-proj.governance.workflows`" in sql
    assert "WHEN MATCHED THEN UPDATE" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql


def test_register_workflow_passes_all_fields_as_params():
    bq = _FakeBQ()
    register_workflow(_wf(), bq_client=bq, project="p", now=_NOW)
    _, params = bq.queries[0]
    param_names = {p.name for p in params}
    for expected in (
        "name",
        "cron",
        "trigger_kind",
        "idempotent",
        "owner_team",
        "business_purpose",
        "expected_freshness_hours",
        "expected_cost_per_run_usd",
        "policy_refs",
        "source_path",
        "status",
        "now",
    ):
        assert expected in param_names


def test_register_workflow_requires_name():
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="workflow.name is required"):
        register_workflow(_wf(name=""), bq_client=bq, project="p", now=_NOW)


def test_register_workflow_uses_custom_dataset():
    bq = _FakeBQ()
    register_workflow(
        _wf(),
        bq_client=bq,
        project="p",
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    sql, _ = bq.queries[0]
    assert "`p.sre.workflows`" in sql


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "name": "wf_demo",
        "cron": "0 * * * *",
        "trigger_kind": "scheduler",
        "idempotent": True,
        "owner_team": "data_owners",
        "business_purpose": "demo",
        "expected_freshness_hours": 6,
        "expected_cost_per_run_usd": 0.1,
        "policy_refs": ["p1"],
        "source_path": "agents/demo.py",
        "status": "active",
    }
    defaults.update(overrides)
    return defaults


def test_lookup_returns_workflow_for_known_name():
    bq = _FakeBQ(rows=[_row(name="wf_a")])
    wf = lookup("wf_a", bq_client=bq, project="p")
    assert wf is not None
    assert wf.name == "wf_a"
    assert wf.idempotent is True
    assert wf.policy_refs == ["p1"]


def test_lookup_returns_none_for_unknown_name():
    bq = _FakeBQ(rows=[])
    assert lookup("wf_ghost", bq_client=bq, project="p") is None


def test_lookup_returns_none_on_bq_error():
    class _RaisingBQ:
        def query(self, sql: str, job_config: Any | None = None) -> Any:
            raise RuntimeError("simulated BQ outage")

    assert lookup("wf_a", bq_client=_RaisingBQ(), project="p") is None


def test_lookup_handles_null_policy_refs():
    bq = _FakeBQ(rows=[_row(policy_refs=None)])
    wf = lookup("wf_demo", bq_client=bq, project="p")
    assert wf is not None
    assert wf.policy_refs == []


# ---------------------------------------------------------------------------
# list_active
# ---------------------------------------------------------------------------


def test_list_active_returns_workflow_list():
    bq = _FakeBQ(rows=[_row(name="wf_a"), _row(name="wf_b", status=None)])
    wfs = list_active(bq_client=bq, project="p")
    assert [w.name for w in wfs] == ["wf_a", "wf_b"]
    assert wfs[1].status == "active"  # NULL coerces to 'active'


def test_list_active_returns_empty_on_bq_error():
    class _RaisingBQ:
        def query(self, sql: str) -> Any:
            raise RuntimeError("boom")

    assert list_active(bq_client=_RaisingBQ(), project="p") == []


# ---------------------------------------------------------------------------
# is_idempotent
# ---------------------------------------------------------------------------


def test_is_idempotent_true_when_flag_explicitly_true():
    bq = _FakeBQ(rows=[_row(idempotent=True)])
    assert is_idempotent("wf_a", bq_client=bq, project="p") is True


def test_is_idempotent_false_when_flag_false():
    bq = _FakeBQ(rows=[_row(idempotent=False)])
    assert is_idempotent("wf_a", bq_client=bq, project="p") is False


def test_is_idempotent_false_when_flag_null():
    """NULL is treated as not-idempotent — safer default."""
    bq = _FakeBQ(rows=[_row(idempotent=None)])
    assert is_idempotent("wf_a", bq_client=bq, project="p") is False


def test_is_idempotent_false_when_workflow_unknown():
    bq = _FakeBQ(rows=[])
    assert is_idempotent("wf_ghost", bq_client=bq, project="p") is False
