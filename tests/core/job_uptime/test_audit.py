"""Smoke tests for the job_uptime sweep orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.job_uptime import audit
from sre_free_mcp.core.job_uptime.checks import WorkflowStatus
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _stub_statuses(monkeypatch, statuses: list[WorkflowStatus]):
    monkeypatch.setattr(
        audit, "query_statuses", lambda bq, p, r, **kw: list(statuses)
    )


def _stub_pause(monkeypatch, pause: dict[str, bool] | None = None):
    monkeypatch.setattr(
        audit, "fetch_scheduler_pause_state", lambda p, r, names: dict(pause or {})
    )


def _sweep(bq, **kwargs) -> audit.AuditResult:
    defaults = {"project": "p", "region": "us-central1", "bq_client": bq, "now": _NOW}
    defaults.update(kwargs)
    return audit.sweep(**defaults)


# ---------------------------------------------------------------------------
# Empty cases
# ---------------------------------------------------------------------------


def test_sweep_with_no_workflows_returns_empty(monkeypatch):
    _stub_statuses(monkeypatch, [])
    _stub_pause(monkeypatch)
    result = _sweep(_FakeBQ())
    assert result.workflows == 0
    assert result.findings == []


def test_sweep_with_healthy_workflow_writes_nothing(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(hours=1),
        last_execution_status="ok",
        last_n_statuses=["ok", "ok", "ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    result = _sweep(bq)
    assert result.workflows == 1
    assert result.findings == []
    assert bq.inserts == []


# ---------------------------------------------------------------------------
# Each rule round-trips into gap_reports
# ---------------------------------------------------------------------------


def test_sweep_writes_missed_run_finding(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(hours=30),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    result = _sweep(bq)
    kinds = {f.gap_kind for f in result.findings}
    assert "workflow_missed_run" in kinds
    inserts = [r for _, rows in bq.inserts for r in rows]
    assert any(r["gap_kind"] == "workflow_missed_run" for r in inserts)


def test_sweep_writes_failing_persistently_finding(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(minutes=10),
        last_execution_status="failed",
        last_n_statuses=["failed", "failed", "failed"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    result = _sweep(bq)
    kinds = {f.gap_kind for f in result.findings}
    assert "workflow_failing_persistently" in kinds


def test_sweep_writes_paused_finding(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(minutes=10),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": True})  # PAUSED
    bq = _FakeBQ()
    result = _sweep(bq)
    kinds = {f.gap_kind for f in result.findings}
    assert "workflow_paused" in kinds


def test_sweep_writes_no_scheduler_finding(monkeypatch):
    """trigger_kind=scheduler + job_name set + pause_state missing → no scheduler."""
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(minutes=10),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {})  # trigger not found
    bq = _FakeBQ()
    result = _sweep(bq)
    kinds = {f.gap_kind for f in result.findings}
    assert "workflow_no_scheduler" in kinds


def test_sweep_writes_stale_registration_finding(monkeypatch):
    s = WorkflowStatus(
        name="wf_dead",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name=None,  # never deployed
        last_execution_at=None,
        last_execution_status=None,
        last_n_statuses=[],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch)
    bq = _FakeBQ()
    result = _sweep(bq)
    kinds = {f.gap_kind for f in result.findings}
    assert "workflow_stale_registration" in kinds


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_sweep_writes_finding_with_scope_workflow(monkeypatch):
    """All job_uptime findings must use scope='workflow' so pipeline_health
    picks them up via the view filter."""
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(hours=30),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    result = _sweep(bq)
    assert all(f.scope == "workflow" for f in result.findings)
    assert all(f.scope_id == "wf_a" for f in result.findings)


def test_sweep_write_false_returns_findings_without_persisting(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(hours=30),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    result = _sweep(bq, write=False)
    assert result.findings
    assert bq.inserts == []


def test_sweep_uses_custom_dataset_when_writing(monkeypatch):
    s = WorkflowStatus(
        name="wf_a",
        cron="0 4 * * *",
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_execution_at=_NOW - timedelta(hours=30),
        last_execution_status="ok",
        last_n_statuses=["ok"],
    )
    _stub_statuses(monkeypatch, [s])
    _stub_pause(monkeypatch, {"wf-a-job-trigger": False})
    bq = _FakeBQ()
    _sweep(bq, tables=GovernanceTables(dataset="sre"))
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project_and_region(monkeypatch):
    _stub_statuses(monkeypatch, [])
    _stub_pause(monkeypatch)
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", region="us-central1", bq_client=bq, now=_NOW)
    with pytest.raises(ValueError, match="region is required"):
        audit.sweep(project="p", region="", bq_client=bq, now=_NOW)
