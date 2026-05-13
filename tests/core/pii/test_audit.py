"""Smoke tests for the PII inspection sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.pii import audit
from sre_free_mcp.core.pii.audit import PiiFinding
from sre_free_mcp.core.pii.targets import PiiTarget
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_inspect(monkeypatch, findings_by_table: dict[str, list[PiiFinding]]):
    def _stub(project: str, target: PiiTarget) -> list[PiiFinding]:
        return list(findings_by_table.get(target.fqn, []))

    monkeypatch.setattr(audit, "inspect_target", _stub)


def _target(
    *,
    dataset: str = "customers",
    table: str = "users",
    not_yet_built: bool = False,
) -> PiiTarget:
    return PiiTarget(dataset=dataset, table=table, not_yet_built=not_yet_built)


def _pii(
    *,
    info_type: str = "EMAIL_ADDRESS",
    likelihood: str = "VERY_LIKELY",
    finding_count: int = 42,
) -> PiiFinding:
    return PiiFinding(
        dataset="customers",
        table="users",
        column_path="email",
        info_type=info_type,
        likelihood=likelihood,
        sample_count=1000,
        finding_count=finding_count,
    )


# ---------------------------------------------------------------------------
# Empty / skip
# ---------------------------------------------------------------------------


def test_sweep_with_no_targets(monkeypatch):
    _patch_inspect(monkeypatch, {})
    result = audit.sweep(project="p", bq_client=_FakeBQ(), targets=[], now=_NOW)
    assert result.targets_scanned == 0


def test_sweep_skips_not_yet_built(monkeypatch):
    _patch_inspect(monkeypatch, {})
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        targets=[_target(not_yet_built=True)],
        now=_NOW,
    )
    assert result.targets_skipped == 1
    assert result.targets_scanned == 0


# ---------------------------------------------------------------------------
# Finding write paths
# ---------------------------------------------------------------------------


def test_sweep_writes_pii_findings_table_when_findings_present(monkeypatch):
    _patch_inspect(monkeypatch, {"customers.users": [_pii()]})
    bq = _FakeBQ()
    audit.sweep(project="p", bq_client=bq, targets=[_target()], now=_NOW)
    assert any(tbl == "p.governance.pii_findings" for tbl, _ in bq.inserts)


def test_sweep_mirrors_high_severity_into_gap_reports(monkeypatch):
    """VERY_LIKELY EMAIL_ADDRESS → critical → mirrored into gap_reports."""
    _patch_inspect(
        monkeypatch,
        {"customers.users": [_pii(info_type="EMAIL_ADDRESS", likelihood="VERY_LIKELY")]},
    )
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, targets=[_target()], now=_NOW
    )
    assert any(f.scope == "pii" and f.severity == "critical" for f in result.mirrored_findings)
    assert any(tbl == "p.governance.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_does_not_mirror_low_findings(monkeypatch):
    """UNLIKELY likelihood → low severity → no gap_reports mirror."""
    _patch_inspect(
        monkeypatch,
        {"customers.users": [_pii(likelihood="UNLIKELY")]},
    )
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, targets=[_target()], now=_NOW
    )
    assert result.mirrored_findings == []
    # Still writes the raw PII finding to pii_findings:
    assert any(tbl == "p.governance.pii_findings" for tbl, _ in bq.inserts)
    # But NOT to gap_reports:
    assert not any(tbl == "p.governance.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch_inspect(monkeypatch, {"customers.users": [_pii()]})
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        targets=[_target()],
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.pii_findings" for tbl, _ in bq.inserts)
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_isolates_per_target_failures(monkeypatch):
    """One target's inspect raising doesn't take the sweep down."""

    def _stub(project: str, target: PiiTarget) -> list[PiiFinding]:
        if target.table == "broken":
            raise RuntimeError("simulated DLP outage")
        return [_pii()]

    monkeypatch.setattr(audit, "inspect_target", _stub)
    targets = [_target(table="broken"), _target(table="ok", dataset="customers")]
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, targets=targets, now=_NOW)
    # broken failed; ok ran → 1 scanned
    assert result.targets_scanned == 1
    assert len(result.pii_findings) == 1


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), targets=[], now=_NOW)
