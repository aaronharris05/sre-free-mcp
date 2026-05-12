"""Smoke tests for the anomaly sweep orchestrator.

Stubs the BQ read + engine score so tests run without sklearn or live
data. Verifies skip-on-not-yet-built, thin-window handling, severity
classification, gap_reports write shape, and the dataset-rename flow-
through.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.anomaly import detector
from sre_free_mcp.core.anomaly.targets import AnomalyTarget
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)


def _target(
    *,
    table: str = "ex.weather",
    metric: str = "temp_f",
    owner: str = "data_owners",
    red: float = 5.0,
    yellow: float = 3.5,
    not_yet_built: bool = False,
) -> AnomalyTarget:
    return AnomalyTarget(
        table=table,
        metric_column=metric,
        timestamp_column="ts",
        lookback_days=14,
        severity_red_z=red,
        severity_yellow_z=yellow,
        owner_team=owner,
        purpose="test",
        not_yet_built=not_yet_built,
    )


class _FakeBQ:
    def __init__(self) -> None:
        self.project = "test-proj"
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def query(self, sql: str, job_config: Any | None = None) -> Any:
        raise AssertionError("Test should patch _read_window directly; query() not stubbed")

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_window(monkeypatch, rows_by_target: dict[str, list[dict[str, Any]]]):
    """Stub _read_window to return canned rows keyed by table name."""

    def _stub(bq, project, target, now):
        return list(rows_by_target.get(target.table, []))

    monkeypatch.setattr(detector, "_read_window", _stub)


def _patch_engine(monkeypatch, *, scores: list[float], model: str = "isolation_forest"):
    def _stub(values):
        return list(scores), model

    monkeypatch.setattr(detector, "engine_score", _stub)


# ---------------------------------------------------------------------------
# Skipping
# ---------------------------------------------------------------------------


def test_not_yet_built_target_is_skipped(monkeypatch):
    targets = [_target(not_yet_built=True)]
    _patch_window(monkeypatch, {})
    _patch_engine(monkeypatch, scores=[])

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.scanned == 0
    assert result.skipped == 1
    assert result.findings == []


def test_empty_window_produces_no_findings(monkeypatch):
    targets = [_target(table="ex.empty")]
    _patch_window(monkeypatch, {"ex.empty": []})
    _patch_engine(monkeypatch, scores=[])

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.scanned == 1
    assert result.findings == []


def test_thin_window_returns_no_findings(monkeypatch):
    """If the engine returns no scores (insufficient_data), no findings fire."""
    targets = [_target(table="ex.thin")]
    rows = [{"ts": f"2026-05-0{i}", "value": float(i)} for i in range(1, 6)]
    _patch_window(monkeypatch, {"ex.thin": rows})
    _patch_engine(monkeypatch, scores=[], model="insufficient_data")

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.scanned == 1
    assert result.findings == []


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


def test_high_severity_flag(monkeypatch):
    """A row whose z-equivalent crosses red threshold gets severity=high."""
    targets = [_target(table="ex.weather", red=5.0, yellow=3.5)]
    # Build a window where the last row will become a large positive z.
    rows = [{"ts": f"2026-05-{i:02d}", "value": float(i)} for i in range(1, 30)]
    rows.append({"ts": "2026-05-30", "value": 1000.0})  # the obvious outlier
    _patch_window(monkeypatch, {"ex.weather": rows})

    # Stub engine: give the outlier (last index) a much bigger raw score.
    scores = [1.0] * (len(rows) - 1) + [50.0]
    _patch_engine(monkeypatch, scores=scores)

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert any(f.severity == "high" for f in result.findings)
    high = [f for f in result.findings if f.severity == "high"][0]
    assert high.details["table"] == "ex.weather"
    assert high.details["owner_team"] == "data_owners"
    assert high.scope == "data"
    assert high.gap_kind == "anomaly_zscore"


def test_no_findings_when_all_z_below_yellow(monkeypatch):
    """If z-scores stay below yellow, no findings fire."""
    targets = [_target(red=10.0, yellow=8.0)]
    rows = [{"ts": f"2026-05-{i:02d}", "value": float(i)} for i in range(1, 31)]
    _patch_window(monkeypatch, {"ex.weather": rows})
    # Tight, normal-ish scores → max z-equivalent well below 8.
    _patch_engine(monkeypatch, scores=[float(i) for i in range(30)])

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.findings == []


def test_rows_with_none_values_are_skipped(monkeypatch):
    """Null metric values must not be scored (would break z-equivalent)."""
    targets = [_target(table="ex.with_nulls")]
    rows = []
    for i in range(35):
        rows.append({"ts": f"2026-05-{i:02d}", "value": float(i) if i % 2 == 0 else None})
    _patch_window(monkeypatch, {"ex.with_nulls": rows})
    _patch_engine(monkeypatch, scores=[1.0] * 18)  # 18 non-null rows

    bq = _FakeBQ()
    # Just verify the sweep doesn't crash on Nones.
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.scanned == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_findings_are_written_to_gap_reports(monkeypatch):
    targets = [_target(table="ex.weather", red=2.0, yellow=1.0)]
    rows = [{"ts": f"2026-05-{i:02d}", "value": float(i)} for i in range(1, 31)]
    rows.append({"ts": "2026-05-31", "value": 1000.0})
    _patch_window(monkeypatch, {"ex.weather": rows})
    scores = [1.0] * 30 + [50.0]
    _patch_engine(monkeypatch, scores=scores)

    bq = _FakeBQ()
    detector.sweep(project="my-proj", bq_client=bq, targets=targets, now=_NOW)

    gap_inserts = [r for tbl, rows in bq.inserts for r in rows if "gap_reports" in tbl]
    assert len(gap_inserts) >= 1
    row = gap_inserts[0]
    assert row["scope"] == "data"
    assert row["gap_kind"] == "anomaly_zscore"
    # details is JSON-stringified
    details = json.loads(row["details"])
    assert details["table"] == "ex.weather"
    assert "z_score" in details


def test_write_findings_false_returns_findings_without_inserting(monkeypatch):
    """Dry-run mode: compute findings but don't persist."""
    targets = [_target(table="ex.weather", red=2.0, yellow=1.0)]
    rows = [{"ts": f"2026-05-{i:02d}", "value": float(i)} for i in range(1, 31)]
    rows.append({"ts": "2026-05-31", "value": 1000.0})
    _patch_window(monkeypatch, {"ex.weather": rows})
    scores = [1.0] * 30 + [50.0]
    _patch_engine(monkeypatch, scores=scores)

    bq = _FakeBQ()
    result = detector.sweep(
        project="my-proj",
        bq_client=bq,
        targets=targets,
        now=_NOW,
        write_findings=False,
    )
    assert len(result.findings) >= 1
    assert bq.inserts == []


def test_custom_governance_dataset_threads_into_findings_write(monkeypatch):
    targets = [_target(red=2.0, yellow=1.0)]
    rows = [{"ts": f"2026-05-{i:02d}", "value": float(i)} for i in range(1, 31)]
    rows.append({"ts": "2026-05-31", "value": 1000.0})
    _patch_window(monkeypatch, {"ex.weather": rows})
    _patch_engine(monkeypatch, scores=[1.0] * 30 + [50.0])

    bq = _FakeBQ()
    detector.sweep(
        project="my-proj",
        bq_client=bq,
        targets=targets,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "my-proj.sre.gap_reports" for tbl, _ in bq.inserts)


# ---------------------------------------------------------------------------
# Per-target isolation + arg validation
# ---------------------------------------------------------------------------


def test_per_target_exception_does_not_abort_sweep(monkeypatch):
    """If reading one target raises, the sweep continues with the next."""
    targets = [_target(table="ex.broken"), _target(table="ex.ok")]

    def _stub_window(bq, project, target, now):
        if target.table == "ex.broken":
            raise RuntimeError("simulated BQ error")
        return []

    monkeypatch.setattr(detector, "_read_window", _stub_window)
    _patch_engine(monkeypatch, scores=[])

    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    assert result.scanned == 2  # both attempted
    assert result.findings == []


def test_sweep_requires_project():
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        detector.sweep(project="", bq_client=bq, targets=[], now=_NOW)


def test_sweep_with_no_targets_returns_empty(monkeypatch):
    bq = _FakeBQ()
    result = detector.sweep(project="p", bq_client=bq, targets=[], now=_NOW)
    assert result.scanned == 0
    assert result.skipped == 0
    assert result.findings == []
