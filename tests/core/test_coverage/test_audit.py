"""Smoke tests for the test_coverage sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.core.test_coverage import audit

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


_SAMPLE_XML = """<?xml version="1.0" ?>
<coverage line-rate="0.50" lines-valid="100" lines-covered="50">
  <packages>
    <package name="app" line-rate="0.5">
      <classes>
        <class filename="app/x.py" name="x" line-rate="0.30" lines-valid="100"/>
      </classes>
    </package>
  </packages>
</coverage>
"""


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def test_sweep_with_no_reports_returns_empty():
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), coverage_reports=[], now=_NOW
    )
    assert result.reports == 0
    assert result.findings == []


def test_sweep_skips_missing_report(tmp_path: Path):
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        coverage_reports=[tmp_path / "does-not-exist.xml"],
        now=_NOW,
    )
    assert result.reports == 0


def test_sweep_writes_findings_for_low_coverage(tmp_path: Path):
    path = tmp_path / "coverage.xml"
    path.write_text(_SAMPLE_XML, encoding="utf-8")
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, coverage_reports=[path], now=_NOW
    )
    kinds = {f.gap_kind for f in result.findings}
    assert "low_overall_coverage" in kinds
    assert "low_module_coverage" in kinds
    assert any(r["scope"] == "test_coverage" for _, rows in bq.inserts for r in rows)


def test_sweep_uses_custom_dataset(tmp_path: Path):
    path = tmp_path / "coverage.xml"
    path.write_text(_SAMPLE_XML, encoding="utf-8")
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        coverage_reports=[path],
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_threads_tunable_thresholds(tmp_path: Path):
    path = tmp_path / "coverage.xml"
    path.write_text(_SAMPLE_XML, encoding="utf-8")
    # Loosen thresholds well below the sample's 0.50 overall + 0.30 file.
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        coverage_reports=[path],
        overall_threshold=0.20,
        module_threshold=0.20,
        now=_NOW,
    )
    assert result.findings == []


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), coverage_reports=[], now=_NOW)
