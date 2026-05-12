"""Unit tests for dependency rules."""

from __future__ import annotations

from sre_free_mcp.core.dependency.checks import Dependency, evaluate


def _d(name: str = "x", spec: str = "") -> Dependency:
    return Dependency(name=name, specifier=spec, source="pyproject.toml")


# ---------------------------------------------------------------------------
# unpinned_dependency
# ---------------------------------------------------------------------------


def test_unpinned_fires_high():
    findings = evaluate(_d(spec=""))
    matched = [f for f in findings if f.gap_kind == "unpinned_dependency"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_pinned_with_specifier_does_not_fire_unpinned():
    findings = evaluate(_d(spec=">=2.7,<3"))
    assert not any(f.gap_kind == "unpinned_dependency" for f in findings)


# ---------------------------------------------------------------------------
# wildcard_dependency
# ---------------------------------------------------------------------------


def test_explicit_wildcard_fires():
    findings = evaluate(_d(spec="*"))
    # First fires unpinned (spec is non-empty but `*` is also wildcard).
    # Actually `*` is non-empty so unpinned doesn't fire — only wildcard.
    matched = [f for f in findings if f.gap_kind == "wildcard_dependency"]
    assert len(matched) == 1


def test_unbounded_lower_fires_wildcard():
    findings = evaluate(_d(spec=">=2.0"))
    matched = [f for f in findings if f.gap_kind == "wildcard_dependency"]
    assert len(matched) == 1
    assert matched[0].severity == "medium"


def test_bounded_range_does_not_fire_wildcard():
    findings = evaluate(_d(spec=">=2.0,<3.0"))
    assert not any(f.gap_kind == "wildcard_dependency" for f in findings)


def test_pin_does_not_fire_wildcard():
    findings = evaluate(_d(spec="==2.7.0"))
    assert not any(f.gap_kind == "wildcard_dependency" for f in findings)


# ---------------------------------------------------------------------------
# pre_release_pin
# ---------------------------------------------------------------------------


def test_pre_release_alpha_fires_low():
    findings = evaluate(_d(spec="==2.0.0a1"))
    matched = [f for f in findings if f.gap_kind == "pre_release_pin"]
    assert len(matched) == 1
    assert matched[0].severity == "low"


def test_pre_release_rc_fires():
    findings = evaluate(_d(spec="==1.0rc2"))
    assert any(f.gap_kind == "pre_release_pin" for f in findings)


def test_stable_pin_does_not_fire_pre_release():
    findings = evaluate(_d(spec="==1.0.0"))
    assert not any(f.gap_kind == "pre_release_pin" for f in findings)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_finding_scope_and_details():
    findings = evaluate(_d(name="django", spec=""))
    f = findings[0]
    assert f.scope == "dependency"
    assert f.scope_id == "pyproject.toml:django"
    assert f.details["package"] == "django"
    assert f.details["source"] == "pyproject.toml"
