"""Unit tests for data_catalog rule logic."""

from __future__ import annotations

from sre_free_mcp.core.data_catalog.checks import evaluate
from sre_free_mcp.core.workflows import Workflow


def _wf(**overrides):
    defaults = {
        "name": "wf_demo",
        "cron": "0 4 * * *",
        "trigger_kind": "scheduler",
        "idempotent": True,
        "owner_team": "data_owners",
        "business_purpose": "Demo workflow.",
        "source_path": "agents/demo.py",
        "status": "active",
    }
    defaults.update(overrides)
    return Workflow(**defaults)


def test_healthy_workflow_produces_no_findings():
    assert evaluate(_wf()) == []


def test_missing_source_path_fires():
    kinds = {f.gap_kind for f in evaluate(_wf(source_path=None))}
    assert "missing_source_path" in kinds


def test_missing_owner_team_fires_high():
    findings = evaluate(_wf(owner_team=None))
    matched = [f for f in findings if f.gap_kind == "missing_owner_team"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_null_idempotent_fires():
    kinds = {f.gap_kind for f in evaluate(_wf(idempotent=None))}
    assert "missing_idempotent_flag" in kinds


def test_explicit_false_idempotent_does_not_fire():
    """False is a valid declaration — only NULL is a gap."""
    kinds = {f.gap_kind for f in evaluate(_wf(idempotent=False))}
    assert "missing_idempotent_flag" not in kinds


def test_scheduler_without_cron_fires_high():
    findings = evaluate(_wf(trigger_kind="scheduler", cron=None))
    matched = [f for f in findings if f.gap_kind == "missing_cron_for_scheduler"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_unparseable_cron_fires_low():
    findings = evaluate(_wf(cron="*/15 9-17 * * 1-5"))  # exotic
    matched = [f for f in findings if f.gap_kind == "unparseable_cron"]
    assert len(matched) == 1
    assert matched[0].severity == "low"


def test_event_trigger_skips_cron_rules():
    kinds = {f.gap_kind for f in evaluate(_wf(trigger_kind="event", cron=None))}
    assert "missing_cron_for_scheduler" not in kinds
    assert "unparseable_cron" not in kinds


def test_findings_use_scope_catalog_and_workflow_name_scope_id():
    findings = evaluate(_wf(owner_team=None, source_path=None))
    assert all(f.scope == "catalog" for f in findings)
    assert all(f.scope_id == "wf_demo" for f in findings)
