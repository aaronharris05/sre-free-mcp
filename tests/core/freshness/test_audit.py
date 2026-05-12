"""Smoke tests for the freshness sweep orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.freshness import audit
from sre_free_mcp.core.freshness.checks import TableFreshness
from sre_free_mcp.core.freshness.targets import FreshnessTarget
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _t(
    *,
    dataset: str = "raw",
    table: str = "weather_hourly",
    cadence: float = 1.5,
    not_yet_built: bool = False,
) -> FreshnessTarget:
    return FreshnessTarget(
        dataset=dataset,
        table=table,
        expected_cadence_hours=cadence,
        owner_team="data_owners",
        purpose="test",
        not_yet_built=not_yet_built,
    )


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_read(
    monkeypatch, snapshots: dict[tuple[str, str], TableFreshness | None]
):
    def _stub(bq, project, target):
        return snapshots.get((target.dataset, target.table))

    monkeypatch.setattr(audit, "read_snapshot", _stub)


# ---------------------------------------------------------------------------
# Empty cases
# ---------------------------------------------------------------------------


def test_sweep_with_no_targets_returns_empty(monkeypatch):
    _patch_read(monkeypatch, {})
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, targets=[], now=_NOW)
    assert result.scanned == 0
    assert result.findings == []


def test_sweep_skips_not_yet_built_targets(monkeypatch):
    _patch_read(monkeypatch, {})
    targets = [
        _t(dataset="raw", table="a"),
        _t(dataset="raw", table="b", not_yet_built=True),
    ]
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.skipped == 1


def test_sweep_with_missing_snapshot_does_not_crash(monkeypatch):
    """read_snapshot returning None (table not found) is benign."""
    _patch_read(monkeypatch, {("raw", "x"): None})
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, targets=[_t(dataset="raw", table="x")], now=_NOW
    )
    assert result.findings == []


# ---------------------------------------------------------------------------
# Findings round-trip
# ---------------------------------------------------------------------------


def test_stale_table_finding_persisted_to_gap_reports(monkeypatch):
    snap = TableFreshness(
        dataset="raw",
        table="weather_hourly",
        last_modified=_NOW - timedelta(hours=10),  # > 5x 1.5h cadence
        row_count=100,
        expected_cadence_hours=1.5,
        owner_team="data_owners",
        purpose="test",
    )
    _patch_read(monkeypatch, {("raw", "weather_hourly"): snap})
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, targets=[_t()], now=_NOW
    )
    assert any(f.gap_kind == "table_stale" for f in result.findings)
    inserts = [r for _, rows in bq.inserts for r in rows]
    assert any(r["scope"] == "freshness" for r in inserts)


def test_write_false_returns_findings_without_persisting(monkeypatch):
    snap = TableFreshness(
        dataset="raw",
        table="weather_hourly",
        last_modified=_NOW - timedelta(hours=10),
        row_count=100,
        expected_cadence_hours=1.5,
        owner_team="data_owners",
        purpose="",
    )
    _patch_read(monkeypatch, {("raw", "weather_hourly"): snap})
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, targets=[_t()], now=_NOW, write=False
    )
    assert result.findings
    assert bq.inserts == []


def test_sweep_uses_custom_dataset(monkeypatch):
    snap = TableFreshness(
        dataset="raw",
        table="weather_hourly",
        last_modified=_NOW - timedelta(hours=10),
        row_count=100,
        expected_cadence_hours=1.5,
        owner_team="data_owners",
        purpose="",
    )
    _patch_read(monkeypatch, {("raw", "weather_hourly"): snap})
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        targets=[_t()],
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_isolates_per_target_exceptions(monkeypatch):
    """If reading one target raises, the sweep continues with the next."""

    def _stub(bq, project, target):
        if target.table == "broken":
            raise RuntimeError("simulated read failure")
        return TableFreshness(
            dataset=target.dataset,
            table=target.table,
            last_modified=_NOW - timedelta(minutes=30),
            row_count=100,
            expected_cadence_hours=target.expected_cadence_hours,
            owner_team=target.owner_team,
            purpose=target.purpose,
        )

    monkeypatch.setattr(audit, "read_snapshot", _stub)
    targets = [_t(table="broken"), _t(table="ok")]
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    # The healthy target reads OK; the broken one is caught.
    assert result.scanned == 1


def test_sweep_requires_project():
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=bq, targets=[], now=_NOW)


# ---------------------------------------------------------------------------
# read_snapshot identifier validation
# ---------------------------------------------------------------------------


def test_read_snapshot_rejects_unsafe_dataset_identifier():
    bq = _FakeBQ()
    bad = FreshnessTarget(
        dataset="raw; DROP TABLE",  # SQL injection attempt
        table="weather_hourly",
        expected_cadence_hours=1.5,
        owner_team="data_owners",
    )
    with pytest.raises(ValueError, match="unsafe BigQuery identifier"):
        audit.read_snapshot(bq, "p", bad)


def test_read_snapshot_rejects_unsafe_project_identifier():
    bq = _FakeBQ()
    target = FreshnessTarget(
        dataset="raw",
        table="weather_hourly",
        expected_cadence_hours=1.5,
        owner_team="data_owners",
    )
    with pytest.raises(ValueError, match="unsafe BigQuery identifier"):
        audit.read_snapshot(bq, "my-proj; DROP", target)
