"""Unit tests for AI-governance rule logic."""

from __future__ import annotations

from sre_free_mcp.core.ai_gov.checks import evaluate
from sre_free_mcp.core.workflows import Workflow


def _wf(**overrides):
    defaults = {
        "name": "wf_demo",
        "cron": "0 4 * * *",
        "trigger_kind": "scheduler",
        "idempotent": True,
        "owner_team": "data_owners",
        "business_purpose": "Reconciles daily customer counts against billing source.",
        "source_path": "agents/demo.py",
        "status": "active",
    }
    defaults.update(overrides)
    return Workflow(**defaults)


def test_workflow_with_proper_business_purpose_no_findings():
    assert evaluate(_wf()) == []


def test_missing_business_purpose_fires_high():
    findings = evaluate(_wf(business_purpose=None))
    matched = [f for f in findings if f.gap_kind == "missing_business_purpose"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_empty_business_purpose_also_fires():
    findings = evaluate(_wf(business_purpose=""))
    assert any(f.gap_kind == "missing_business_purpose" for f in findings)


def test_too_short_business_purpose_fires_medium():
    findings = evaluate(_wf(business_purpose="TODO"))
    matched = [f for f in findings if f.gap_kind == "business_purpose_too_terse"]
    assert len(matched) == 1
    assert matched[0].severity == "medium"


def test_min_chars_threshold_is_tunable():
    """A 35-char purpose passes default 30 but not a stricter 50."""
    long = "Reconciles billing every morning at 4am."  # 40 chars
    assert evaluate(_wf(business_purpose=long)) == []
    findings = evaluate(_wf(business_purpose=long), business_purpose_min_chars=50)
    assert any(f.gap_kind == "business_purpose_too_terse" for f in findings)


def test_missing_policy_refs_does_not_fire_by_default():
    """Most orgs don't require policy_refs; default is False."""
    findings = evaluate(_wf(policy_refs=[]))
    assert not any(f.gap_kind == "missing_policy_refs" for f in findings)


def test_missing_policy_refs_fires_when_required():
    findings = evaluate(_wf(policy_refs=[]), require_policy_refs=True)
    assert any(f.gap_kind == "missing_policy_refs" for f in findings)


def test_present_policy_refs_pass_when_required():
    findings = evaluate(_wf(policy_refs=["policy-01-2.4"]), require_policy_refs=True)
    assert not any(f.gap_kind == "missing_policy_refs" for f in findings)


def test_findings_use_scope_ai_gov():
    findings = evaluate(_wf(business_purpose=None))
    assert all(f.scope == "ai_gov" for f in findings)
    assert all(f.scope_id == "wf_demo" for f in findings)
