"""Smoke tests for the ai_gov sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.ai_gov import audit
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
        "business_purpose": "Reconciles daily customer counts against billing source.",
        "source_path": "agents/x.py",
        "status": "active",
    }
    defaults.update(overrides)
    return Workflow(**defaults)


def test_sweep_with_no_workflows_returns_empty(monkeypatch):
    _patch_workflows(monkeypatch, [])
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    assert result.workflows == 0


def test_sweep_persists_missing_business_purpose(monkeypatch):
    _patch_workflows(monkeypatch, [_wf(business_purpose=None)])
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(f.gap_kind == "missing_business_purpose" for f in result.findings)
    assert any(r["scope"] == "ai_gov" for _, rows in bq.inserts for r in rows)


def test_sweep_threads_through_tunable_min_chars(monkeypatch):
    """Tightening the min-chars threshold catches a workflow the default
    passes."""
    _patch_workflows(monkeypatch, [_wf(business_purpose="Daily ingest job.")])
    bq = _FakeBQ()
    # Default passes (17 chars > 30? no, fails default too — pick a longer one).
    # Use a 35-char purpose so default passes, tight threshold fires.
    _patch_workflows(
        monkeypatch,
        [_wf(business_purpose="Reconciles billing every morning at 4am.")],
    )
    result = audit.sweep(
        project="p", bq_client=bq, now=_NOW, business_purpose_min_chars=50
    )
    assert any(f.gap_kind == "business_purpose_too_terse" for f in result.findings)


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch_workflows(monkeypatch, [_wf(business_purpose=None)])
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
