"""Unit tests for the rollup email rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from sre_free_mcp.core.rollup.audit import RollupSummary
from sre_free_mcp.core.rollup.email import build_bodies, build_subject

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _empty_summary() -> RollupSummary:
    return RollupSummary(
        generated_at=_NOW,
        window_days=7,
        open_findings_total=0,
        open_findings_by_scope={},
        open_findings_by_severity={},
        open_incidents=0,
        pending_approvals=0,
    )


def _populated_summary() -> RollupSummary:
    return RollupSummary(
        generated_at=_NOW,
        window_days=7,
        open_findings_total=5,
        open_findings_by_scope={"freshness": 3, "workflow": 2},
        open_findings_by_severity={"high": 4, "medium": 1},
        open_incidents=1,
        pending_approvals=2,
        top_findings=[
            {
                "severity": "high",
                "scope": "workflow",
                "scope_id": "wf_a",
                "gap_kind": "workflow_missed_run",
            },
            {
                "severity": "high",
                "scope": "freshness",
                "scope_id": "raw.weather_hourly",
                "gap_kind": "table_stale",
            },
        ],
    )


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


def test_subject_all_clear():
    assert "All clear" in build_subject(_empty_summary())


def test_subject_includes_counts():
    s = build_subject(_populated_summary())
    assert "5 open findings" in s
    assert "1 open incidents" in s


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


def test_text_body_lists_severity_and_scope_counts():
    text, _ = build_bodies(_populated_summary(), project="my-proj")
    assert "high" in text
    assert "freshness" in text
    assert "workflow" in text
    assert "Project: my-proj" in text


def test_text_body_lists_top_findings():
    text, _ = build_bodies(_populated_summary(), project="p")
    assert "wf_a" in text
    assert "raw.weather_hourly" in text


def test_empty_summary_text_has_none_markers():
    text, _ = build_bodies(_empty_summary(), project="p")
    assert "(none)" in text


def test_html_body_includes_count_tables():
    _, html = build_bodies(_populated_summary(), project="p")
    assert "<table" in html
    assert "freshness" in html
    assert "wf_a" in html


def test_html_body_escapes_project_name():
    _, html = build_bodies(_empty_summary(), project="<script>alert(1)</script>")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
