"""Unit tests for test_coverage rules."""

from __future__ import annotations

from sre_free_mcp.core.test_coverage.checks import CoverageSnapshot, FileCoverage, evaluate


def _snap(*, overall: float, files=None) -> CoverageSnapshot:
    return CoverageSnapshot(
        source="coverage.xml",
        overall_line_rate=overall,
        files=files or [],
    )


def _f(name: str, rate: float, lines: int = 100) -> FileCoverage:
    return FileCoverage(filename=name, line_rate=rate, lines_valid=lines)


# ---------------------------------------------------------------------------
# low_overall_coverage
# ---------------------------------------------------------------------------


def test_low_overall_coverage_fires_high():
    findings = evaluate(_snap(overall=0.50))
    matched = [f for f in findings if f.gap_kind == "low_overall_coverage"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_overall_above_threshold_does_not_fire():
    assert evaluate(_snap(overall=0.85)) == []


def test_overall_threshold_is_tunable():
    snap = _snap(overall=0.70)
    # default 0.80 → fires
    assert any(f.gap_kind == "low_overall_coverage" for f in evaluate(snap))
    # custom 0.50 → does not fire
    assert not any(
        f.gap_kind == "low_overall_coverage"
        for f in evaluate(snap, overall_threshold=0.50)
    )


# ---------------------------------------------------------------------------
# low_module_coverage
# ---------------------------------------------------------------------------


def test_low_module_coverage_fires_medium():
    findings = evaluate(
        _snap(overall=0.95, files=[_f("x.py", 0.40, lines=100)])
    )
    matched = [f for f in findings if f.gap_kind == "low_module_coverage"]
    assert len(matched) == 1
    assert matched[0].severity == "medium"


def test_high_module_coverage_does_not_fire():
    findings = evaluate(
        _snap(overall=0.95, files=[_f("x.py", 0.80, lines=100)])
    )
    assert not any(f.gap_kind == "low_module_coverage" for f in findings)


def test_tiny_files_are_skipped_for_module_check():
    """A 10-line file with 1 uncovered line shouldn't trigger a finding."""
    findings = evaluate(
        _snap(overall=0.95, files=[_f("x.py", 0.50, lines=10)]),
        min_lines_for_module_check=20,
    )
    assert not any(f.gap_kind == "low_module_coverage" for f in findings)


def test_module_threshold_is_tunable():
    snap = _snap(overall=0.95, files=[_f("x.py", 0.65, lines=100)])
    # default 0.60 → no fire
    assert not any(f.gap_kind == "low_module_coverage" for f in evaluate(snap))
    # tight 0.70 → fires
    assert any(
        f.gap_kind == "low_module_coverage"
        for f in evaluate(snap, module_threshold=0.70)
    )


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_findings_use_scope_test_coverage():
    findings = evaluate(_snap(overall=0.50, files=[_f("x.py", 0.40, lines=100)]))
    assert all(f.scope == "test_coverage" for f in findings)


def test_module_finding_scope_id_includes_filename():
    findings = evaluate(_snap(overall=0.95, files=[_f("app/x.py", 0.40, lines=100)]))
    matched = [f for f in findings if f.gap_kind == "low_module_coverage"]
    assert matched[0].scope_id == "coverage.xml:app/x.py"
