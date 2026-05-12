"""Smoke tests for the incident lifecycle sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.incidents import audit
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []
        self.updates: list[tuple[str, list[Any]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        params = list(getattr(job_config, "query_parameters", None) or [])
        self.updates.append((sql, params))
        return _FakeJob()


class _FakeJob:
    def result(self) -> list[Any]:
        return []


def _patch(monkeypatch, *, candidates: list, open_incidents: list):
    monkeypatch.setattr(
        audit,
        "_read_unresolved_severity_workflows",
        lambda bq, p, tables, severity: list(candidates),
    )
    monkeypatch.setattr(
        audit, "_read_open_incidents", lambda bq, p, tables: list(open_incidents)
    )


# ---------------------------------------------------------------------------
# Opening incidents
# ---------------------------------------------------------------------------


def test_sweep_opens_incident_for_new_candidate(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[
            {
                "workflow_name": "wf_a",
                "finding_ids": ["f1", "f2"],
                "summary": "critical missed_run on wf_a",
            }
        ],
        open_incidents=[],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.opened == 1
    assert result.closed == 0
    assert len(result.new_incident_ids) == 1
    inserted = [r for tbl, rows in bq.inserts for r in rows if "incidents" in tbl]
    assert len(inserted) == 1
    assert inserted[0]["workflow_name"] == "wf_a"
    assert inserted[0]["linked_finding_ids"] == ["f1", "f2"]


def test_sweep_does_not_double_open_for_workflow_with_existing_open_incident(monkeypatch):
    """A workflow that's already got an open incident gets no new row."""
    _patch(
        monkeypatch,
        candidates=[
            {"workflow_name": "wf_a", "finding_ids": ["f1"], "summary": "x"}
        ],
        open_incidents=[
            {
                "id": "existing-1",
                "workflow_name": "wf_a",
                "scope": None,
                "opened_at": _NOW - timedelta(hours=1),
                "severity": "critical",
                "summary": "old",
                "linked_finding_ids": ["old-1"],
            }
        ],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.opened == 0


def test_sweep_opens_per_candidate_workflow(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[
            {"workflow_name": "wf_a", "finding_ids": [], "summary": "x"},
            {"workflow_name": "wf_b", "finding_ids": [], "summary": "y"},
        ],
        open_incidents=[],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.opened == 2


# ---------------------------------------------------------------------------
# Closing incidents
# ---------------------------------------------------------------------------


def test_sweep_closes_incident_after_quiet_window(monkeypatch):
    """An incident opened > close_quiet_hours ago gets closed."""
    _patch(
        monkeypatch,
        candidates=[],
        open_incidents=[
            {
                "id": "stale-1",
                "workflow_name": "wf_a",
                "scope": None,
                "opened_at": _NOW - timedelta(hours=48),  # > 24h
                "severity": "high",
                "summary": "x",
                "linked_finding_ids": [],
            }
        ],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.closed == 1
    assert "stale-1" in result.closed_incident_ids
    assert bq.updates  # UPDATE SQL fired


def test_sweep_does_not_close_recently_opened_incident(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[],
        open_incidents=[
            {
                "id": "fresh-1",
                "workflow_name": "wf_a",
                "scope": None,
                "opened_at": _NOW - timedelta(hours=3),
                "severity": "high",
                "summary": "x",
                "linked_finding_ids": [],
            }
        ],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.closed == 0


def test_close_quiet_hours_is_tunable(monkeypatch):
    """A 6h-old incident closes under quiet_hours=4 but not the default 24."""
    _patch(
        monkeypatch,
        candidates=[],
        open_incidents=[
            {
                "id": "med-1",
                "workflow_name": "wf_a",
                "scope": None,
                "opened_at": _NOW - timedelta(hours=6),
                "severity": "high",
                "summary": "x",
                "linked_finding_ids": [],
            }
        ],
    )
    bq = _FakeBQ()
    assert audit.sweep(project="p", bq_client=bq, now=_NOW).closed == 0
    assert audit.sweep(
        project="p", bq_client=bq, now=_NOW, close_quiet_hours=4
    ).closed == 1


# ---------------------------------------------------------------------------
# Output shape + cross-cutting
# ---------------------------------------------------------------------------


def test_sweep_open_count_reflects_post_tick_state(monkeypatch):
    """open_count = existing - closed + opened."""
    _patch(
        monkeypatch,
        candidates=[
            {"workflow_name": "wf_new", "finding_ids": [], "summary": "x"}
        ],
        open_incidents=[
            {
                "id": "stale-1",
                "workflow_name": "wf_old",
                "scope": None,
                "opened_at": _NOW - timedelta(hours=48),
                "severity": "high",
                "summary": "x",
                "linked_finding_ids": [],
            }
        ],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    # 1 existing - 1 closed + 1 opened = 1
    assert result.open_count == 1


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[
            {"workflow_name": "wf_a", "finding_ids": [], "summary": "x"}
        ],
        open_incidents=[],
    )
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any("p.sre.incidents" in tbl for tbl, _ in bq.inserts)


def test_sweep_requires_project(monkeypatch):
    _patch(monkeypatch, candidates=[], open_incidents=[])
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), now=_NOW)


def test_open_incident_returns_uuid_string():
    bq = _FakeBQ()
    incident_id = audit.open_incident(
        bq_client=bq,
        project="p",
        tables=GovernanceTables(),
        workflow_name="wf_x",
        severity="critical",
        summary="boom",
        linked_finding_ids=["f1"],
        now=_NOW,
    )
    assert isinstance(incident_id, str)
    assert len(incident_id) >= 32
