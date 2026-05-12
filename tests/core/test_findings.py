"""Unit tests for the shared gap_reports writer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.findings import write_findings
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self, raise_errors: list[Any] | None = None) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []
        self._return = raise_errors or []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return list(self._return)


def _f(scope: str = "data", scope_id: str = "t:c", gap_kind: str = "anomaly_zscore") -> Finding:
    return Finding(
        scope=scope,
        scope_id=scope_id,
        gap_kind=gap_kind,
        severity="high",
        details={"as_of": "2026-05-12T00:00:00+00:00", "value": 1.0},
    )


def test_write_findings_inserts_to_default_dataset():
    bq = _FakeBQ()
    write_findings(bq, [_f()], project="p", generated_at=_NOW)
    assert len(bq.inserts) == 1
    table, rows = bq.inserts[0]
    assert table == "p.governance.gap_reports"
    assert len(rows) == 1


def test_write_findings_uses_custom_dataset():
    bq = _FakeBQ()
    write_findings(
        bq,
        [_f()],
        project="p",
        generated_at=_NOW,
        tables=GovernanceTables(dataset="sre"),
    )
    table, _ = bq.inserts[0]
    assert table == "p.sre.gap_reports"


def test_write_findings_skips_when_empty():
    bq = _FakeBQ()
    write_findings(bq, [], project="p", generated_at=_NOW)
    assert bq.inserts == []


def test_write_findings_stringifies_details_as_json():
    bq = _FakeBQ()
    write_findings(bq, [_f()], project="p", generated_at=_NOW)
    row = bq.inserts[0][1][0]
    assert isinstance(row["details"], str)
    parsed = json.loads(row["details"])
    assert parsed["value"] == 1.0


def test_write_findings_id_is_deterministic_across_runs():
    """Same (scope, scope_id, gap_kind, as_of, generated_at) → same UUID."""
    bq1 = _FakeBQ()
    bq2 = _FakeBQ()
    write_findings(bq1, [_f()], project="p", generated_at=_NOW)
    write_findings(bq2, [_f()], project="p", generated_at=_NOW)
    assert bq1.inserts[0][1][0]["id"] == bq2.inserts[0][1][0]["id"]


def test_write_findings_id_differs_across_generated_at():
    bq = _FakeBQ()
    now2 = datetime(2026, 5, 12, 13, 0, tzinfo=UTC)
    write_findings(bq, [_f()], project="p", generated_at=_NOW)
    write_findings(bq, [_f()], project="p", generated_at=now2)
    assert bq.inserts[0][1][0]["id"] != bq.inserts[1][1][0]["id"]


def test_write_findings_passes_source_through():
    bq = _FakeBQ()
    write_findings(
        bq, [_f()], project="p", generated_at=_NOW, source="my.audit.bot"
    )
    assert bq.inserts[0][1][0]["source"] == "my.audit.bot"


def test_write_findings_raises_on_bq_errors():
    bq = _FakeBQ(raise_errors=[{"index": 0, "errors": ["bad row"]}])
    with pytest.raises(RuntimeError, match="gap_reports insert failed"):
        write_findings(bq, [_f()], project="p", generated_at=_NOW)
