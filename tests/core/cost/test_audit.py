"""Smoke tests for the cost-anomaly sweep orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.cost import audit
from sre_free_mcp.core.cost.audit import CostRow
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_rows(monkeypatch, rows: list[CostRow]):
    monkeypatch.setattr(audit, "read_rows", lambda bq, p, tables=None: list(rows))


def _row(**overrides: Any) -> CostRow:
    defaults = {
        "usage_date": "2026-05-12",
        "service": "Cloud Run",
        "workflow_name": "wf_a",
        "cost_usd": 100.0,
        "baseline_avg_28d": 10.0,
        "z_score": 6.0,
    }
    defaults.update(overrides)
    return CostRow(**defaults)


def test_sweep_with_no_rows_returns_empty(monkeypatch):
    _patch_rows(monkeypatch, [])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.scanned == 0
    assert result.findings == []


def test_sweep_writes_cost_finding_to_gap_reports(monkeypatch):
    _patch_rows(monkeypatch, [_row()])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(f.gap_kind == "cost_anomaly" for f in result.findings)
    assert any(r["scope"] == "cost" for _, rows in bq.inserts for r in rows)


def test_sweep_filters_out_quiet_rows(monkeypatch):
    """A row at z=1 produces no finding; cost_daily can return many rows
    but only the anomalous ones surface."""
    _patch_rows(monkeypatch, [_row(z_score=1.0), _row(workflow_name="wf_b", z_score=6.0)])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.scanned == 2
    assert len(result.findings) == 1
    assert result.findings[0].scope_id == "Cloud Run:wf_b"


def test_write_false_returns_findings_without_persisting(monkeypatch):
    _patch_rows(monkeypatch, [_row()])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW, write=False)
    assert result.findings
    assert bq.inserts == []


def test_custom_thresholds_thread_through(monkeypatch):
    """A row at z=4 fires under default but not under a tightened (5,8)
    pair."""
    _patch_rows(monkeypatch, [_row(z_score=4.0)])
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, now=_NOW, z_yellow=5.0, z_red=8.0
    )
    assert result.findings == []


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch_rows(monkeypatch, [_row()])
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project():
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=bq, now=_NOW)


def test_read_rows_returns_empty_on_bq_error():
    class _RaisingBQ:
        def query(self, sql: str) -> Any:
            raise RuntimeError("simulated")

    assert audit.read_rows(_RaisingBQ(), "p") == []
