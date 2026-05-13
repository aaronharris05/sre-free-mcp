"""Smoke tests for the security (SCC forwarder) sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.security import audit
from sre_free_mcp.core.security.audit import SccFinding
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _scc(
    *,
    severity: str = "HIGH",
    category: str = "PUBLIC_BUCKET",
    resource: str = "//storage.googleapis.com/projects/p/buckets/data",
) -> SccFinding:
    return SccFinding(
        name=f"organizations/123/sources/-/findings/{category}-1",
        category=category,
        severity=severity,
        resource_name=resource,
        event_time=_NOW,
        external_uri="https://console.cloud.google.com/security/...",
        state="ACTIVE",
    )


def _patch(monkeypatch, findings: list[SccFinding]):
    monkeypatch.setattr(audit, "list_active_scc_findings", lambda org: list(findings))


def test_sweep_with_no_org_id_is_noop(monkeypatch):
    """SCC requires an org_id; without it the sweep returns cleanly."""
    monkeypatch.setattr(
        audit, "list_active_scc_findings", lambda org: pytest.fail("should not be called")
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW, organization_id="")
    assert result.active_findings == 0
    assert result.findings == []
    assert bq.inserts == []


def test_sweep_with_no_active_findings_returns_empty(monkeypatch):
    _patch(monkeypatch, [])
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), organization_id="123", now=_NOW
    )
    assert result.active_findings == 0


def test_sweep_writes_findings_with_scope_security(monkeypatch):
    _patch(monkeypatch, [_scc()])
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, organization_id="123", now=_NOW
    )
    assert result.active_findings == 1
    assert any(f.scope == "security" for f in result.findings)
    assert any(r["scope"] == "security" for _, rows in bq.inserts for r in rows)


def test_sweep_maps_scc_severity_to_internal(monkeypatch):
    _patch(monkeypatch, [
        _scc(severity="CRITICAL"),
        _scc(severity="HIGH", category="OPEN_FIREWALL"),
        _scc(severity="MEDIUM", category="OUTDATED_LIBRARY"),
        _scc(severity="LOW", category="WEAK_CREDENTIALS"),
    ])
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, organization_id="123", now=_NOW
    )
    severities = {f.severity for f in result.findings}
    assert severities == {"critical", "high", "medium", "low"}


def test_sweep_uses_category_as_gap_kind(monkeypatch):
    _patch(monkeypatch, [_scc(category="PUBLIC_BUCKET")])
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), organization_id="123", now=_NOW
    )
    assert result.findings[0].gap_kind == "public_bucket"


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch(monkeypatch, [_scc()])
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        organization_id="123",
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project(monkeypatch):
    _patch(monkeypatch, [])
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), now=_NOW)
