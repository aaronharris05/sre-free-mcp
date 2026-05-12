"""Smoke tests for the data_catalog sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.data_catalog import audit
from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.core.workflows import Workflow

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_workflows(monkeypatch, workflows: list[Workflow]):
    monkeypatch.setattr(audit, "list_active", lambda **kw: list(workflows))


def _wf(**overrides):
    defaults = {
        "name": "wf_a",
        "cron": "0 4 * * *",
        "trigger_kind": "scheduler",
        "idempotent": True,
        "owner_team": "data_owners",
        "business_purpose": "x",
        "source_path": "agents/x.py",
        "status": "active",
    }
    defaults.update(overrides)
    return Workflow(**defaults)


def test_sweep_with_no_workflows_returns_empty(monkeypatch):
    _patch_workflows(monkeypatch, [])
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    assert result.workflows == 0
    assert result.findings == []


def test_sweep_with_healthy_workflow_writes_nothing(monkeypatch):
    _patch_workflows(monkeypatch, [_wf()])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert result.findings == []
    assert bq.inserts == []


def test_sweep_persists_finding_for_broken_workflow(monkeypatch):
    _patch_workflows(monkeypatch, [_wf(owner_team=None)])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(f.scope == "catalog" for f in result.findings)
    assert any(r["scope"] == "catalog" for _, rows in bq.inserts for r in rows)


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch_workflows(monkeypatch, [_wf(owner_team=None)])
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project(monkeypatch):
    _patch_workflows(monkeypatch, [])
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), now=_NOW)
