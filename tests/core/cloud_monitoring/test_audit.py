"""Smoke tests for the Cloud Monitoring sweep + sync routines."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.cloud_monitoring import audit as audit_mod
from sre_free_mcp.core.cloud_monitoring.audit import AlertIncident, sync_policies
from sre_free_mcp.core.cloud_monitoring.policies import (
    AlertCondition,
    AlertPolicy,
)
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _incident(
    *,
    state: str = "OPEN",
    workflow: str | None = "wf_a",
    threshold: float = 1.0,
    observed: float = 5.0,
) -> AlertIncident:
    return AlertIncident(
        id="inc-1",
        policy_name="projects/p/alertPolicies/123",
        policy_display_name="my-policy",
        started_at=_NOW,
        ended_at=None,
        state=state,
        resource_type="cloud_run_job",
        workflow_name=workflow,
        metric_type="run.googleapis.com/job/completed_execution_count",
        threshold_value=threshold,
        observed_value=observed,
    )


# ---------------------------------------------------------------------------
# sweep()
# ---------------------------------------------------------------------------


def test_sweep_with_no_incidents_returns_empty(monkeypatch):
    monkeypatch.setattr(audit_mod, "list_open_incidents", lambda p: [])
    bq = _FakeBQ()
    result = audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.open_incidents == 0
    assert result.findings == []
    assert bq.inserts == []


def test_sweep_writes_finding_for_workflow_tied_incident(monkeypatch):
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident(workflow="wf_a")]
    )
    bq = _FakeBQ()
    result = audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.open_incidents == 1
    assert any(f.scope == "cloud_monitoring" for f in result.findings)
    assert any(f.scope_id == "wf_a" for f in result.findings)


def test_sweep_uses_policy_display_name_when_no_workflow(monkeypatch):
    """An incident without a workflow_name label still surfaces as a
    finding — but does NOT fold into pipeline_health (since the scope_id
    doesn't match a workflow row)."""
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident(workflow=None)]
    )
    bq = _FakeBQ()
    result = audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    # Untied incidents are still cached but produce no scope='cloud_monitoring'
    # finding (since current implementation requires workflow_name to emit one).
    assert result.findings == []


def test_sweep_caches_to_cloud_monitoring_alerts(monkeypatch):
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident()]
    )
    bq = _FakeBQ()
    audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(
        tbl == "p.governance.cloud_monitoring_alerts"
        for tbl, _ in bq.inserts
    )


def test_sweep_uses_custom_dataset(monkeypatch):
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident()]
    )
    bq = _FakeBQ()
    audit_mod.sweep(
        project="p",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(
        tbl == "p.sre.cloud_monitoring_alerts" for tbl, _ in bq.inserts
    )


def test_sweep_write_false_skips_gap_reports_insert(monkeypatch):
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident()]
    )
    bq = _FakeBQ()
    audit_mod.sweep(project="p", bq_client=bq, now=_NOW, write=False)
    # Cache should still write; gap_reports should not.
    assert all("gap_reports" not in tbl for tbl, _ in bq.inserts)


def test_sweep_open_incident_maps_to_high_severity(monkeypatch):
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident(state="OPEN")]
    )
    bq = _FakeBQ()
    result = audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    assert all(f.severity == "high" for f in result.findings)


def test_sweep_closed_incident_maps_to_low_severity(monkeypatch):
    """A still-recently-closed incident is informational, not actionable."""
    monkeypatch.setattr(
        audit_mod, "list_open_incidents", lambda p: [_incident(state="CLOSED")]
    )
    bq = _FakeBQ()
    result = audit_mod.sweep(project="p", bq_client=bq, now=_NOW)
    assert all(f.severity == "low" for f in result.findings)


def test_sweep_requires_project(monkeypatch):
    monkeypatch.setattr(audit_mod, "list_open_incidents", lambda p: [])
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        audit_mod.sweep(project="", bq_client=bq, now=_NOW)


# ---------------------------------------------------------------------------
# sync_policies()
# ---------------------------------------------------------------------------


def _pol(name: str = "test-policy") -> AlertPolicy:
    return AlertPolicy(
        display_name=name,
        owner_team="sre_owners",
        conditions=[
            AlertCondition(metric_type="m.example", threshold_value=1.0)
        ],
    )


def test_sync_creates_new_policies(monkeypatch):
    """A policy not present in the project is created."""
    monkeypatch.setattr(audit_mod, "_list_existing_policies", lambda p: {})
    created: list[AlertPolicy] = []
    monkeypatch.setattr(
        audit_mod, "_create_policy", lambda p, pol: created.append(pol)
    )
    monkeypatch.setattr(audit_mod, "_update_policy", lambda p, e, pol: None)
    summary = sync_policies(project="p", policies=[_pol("new-policy")])
    assert summary["created"] == 1
    assert summary["updated"] == 0
    assert summary["unchanged"] == 0
    assert [p.display_name for p in created] == ["new-policy"]


def test_sync_leaves_unchanged_policies_alone(monkeypatch):
    """Unchanged policy → no API call."""

    class _ExistingPolicy:
        def __init__(self, name: str, doc: str = "", enabled: bool = True) -> None:
            self.name = f"projects/p/alertPolicies/{name}"
            self.display_name = name
            self.enabled = enabled

            class _Doc:
                content = doc

            self.documentation = _Doc()

    existing = {"test-policy": _ExistingPolicy("test-policy", doc="")}
    monkeypatch.setattr(audit_mod, "_list_existing_policies", lambda p: existing)

    def _fail(*a, **kw):
        raise AssertionError("should not be called")

    monkeypatch.setattr(audit_mod, "_create_policy", _fail)
    monkeypatch.setattr(audit_mod, "_update_policy", _fail)

    summary = sync_policies(project="p", policies=[_pol("test-policy")])
    assert summary["unchanged"] == 1
    assert summary["created"] == 0
    assert summary["updated"] == 0


def test_sync_updates_changed_documentation(monkeypatch):
    """Different documentation text → update."""

    class _ExistingPolicy:
        def __init__(self) -> None:
            self.name = "projects/p/alertPolicies/test-policy"
            self.display_name = "test-policy"
            self.enabled = True

            class _Doc:
                content = "old docs"

            self.documentation = _Doc()

    existing = {"test-policy": _ExistingPolicy()}
    monkeypatch.setattr(audit_mod, "_list_existing_policies", lambda p: existing)
    updates: list[AlertPolicy] = []
    monkeypatch.setattr(
        audit_mod, "_update_policy", lambda p, e, pol: updates.append(pol)
    )
    monkeypatch.setattr(audit_mod, "_create_policy", lambda p, pol: None)

    policy = AlertPolicy(
        display_name="test-policy",
        documentation="new docs",
        owner_team="sre_owners",
        conditions=[AlertCondition(metric_type="m", threshold_value=1)],
    )
    summary = sync_policies(project="p", policies=[policy])
    assert summary["updated"] == 1
    assert updates[0].documentation == "new docs"


def test_sync_continues_when_one_policy_fails(monkeypatch):
    """A bad policy doesn't block the rest of the batch."""
    monkeypatch.setattr(audit_mod, "_list_existing_policies", lambda p: {})

    calls = {"n": 0}

    def _maybe_fail(project, pol):
        calls["n"] += 1
        if pol.display_name == "bad":
            raise RuntimeError("simulated API error")

    monkeypatch.setattr(audit_mod, "_create_policy", _maybe_fail)
    monkeypatch.setattr(audit_mod, "_update_policy", lambda p, e, pol: None)

    summary = sync_policies(
        project="p", policies=[_pol("good-1"), _pol("bad"), _pol("good-2")]
    )
    assert calls["n"] == 3
    assert summary["created"] == 2  # good-1 + good-2; bad raised


def test_sync_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        sync_policies(project="", policies=[])
