"""Unit tests for the freshness rule logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sre_free_mcp.core.freshness.checks import TableFreshness, evaluate

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _snap(
    *,
    dataset: str = "raw",
    table: str = "weather_hourly",
    last_modified: datetime | None,
    row_count: int = 100,
    cadence: float = 1.5,
) -> TableFreshness:
    return TableFreshness(
        dataset=dataset,
        table=table,
        last_modified=last_modified,
        row_count=row_count,
        expected_cadence_hours=cadence,
        owner_team="data_owners",
        purpose="test",
    )


# ---------------------------------------------------------------------------
# Stale rule
# ---------------------------------------------------------------------------


def test_table_stale_high_at_2x_cadence():
    """Cadence=1.5h, last_modified 4h ago → 2.67x → high severity."""
    s = _snap(last_modified=_NOW - timedelta(hours=4))
    findings = evaluate(s, _NOW)
    stale = [f for f in findings if f.gap_kind == "table_stale"]
    assert len(stale) == 1
    assert stale[0].severity == "high"


def test_table_stale_critical_at_5x_cadence():
    """Cadence=1.5h, last_modified 10h ago → 6.67x → critical."""
    s = _snap(last_modified=_NOW - timedelta(hours=10))
    findings = evaluate(s, _NOW)
    stale = [f for f in findings if f.gap_kind == "table_stale"]
    assert len(stale) == 1
    assert stale[0].severity == "critical"


def test_table_within_cadence_produces_no_stale():
    s = _snap(last_modified=_NOW - timedelta(minutes=30))
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "table_stale" not in kinds


def test_never_modified_table_produces_no_stale():
    """last_modified=None — caller policy is not to flag (could be a
    new empty staging table)."""
    s = _snap(last_modified=None)
    assert evaluate(s, _NOW) == []


def test_custom_multipliers_change_severity_threshold():
    """A 3x cadence age fires critical under a tight red=2.5 multiplier."""
    s = _snap(last_modified=_NOW - timedelta(hours=5))  # ~3.3x
    out = evaluate(
        s, _NOW, stale_yellow_multiplier=1.5, stale_red_multiplier=2.5
    )
    stale = [f for f in out if f.gap_kind == "table_stale"]
    assert len(stale) == 1
    assert stale[0].severity == "critical"


# ---------------------------------------------------------------------------
# Empty rule
# ---------------------------------------------------------------------------


def test_empty_table_fires_after_24h():
    s = _snap(last_modified=_NOW - timedelta(hours=30), row_count=0, cadence=24)
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "table_empty" in kinds


def test_empty_table_does_not_fire_within_window():
    s = _snap(last_modified=_NOW - timedelta(hours=3), row_count=0, cadence=24)
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "table_empty" not in kinds


def test_non_empty_table_does_not_fire_empty_rule():
    s = _snap(last_modified=_NOW - timedelta(hours=48), row_count=42, cadence=24)
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "table_empty" not in kinds


def test_empty_min_hours_is_configurable():
    """Tighter window catches an empty table the default would tolerate."""
    s = _snap(last_modified=_NOW - timedelta(hours=4), row_count=0, cadence=24)
    assert "table_empty" not in {f.gap_kind for f in evaluate(s, _NOW)}
    out = evaluate(s, _NOW, empty_min_hours=2.0)
    assert "table_empty" in {f.gap_kind for f in out}


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_findings_use_scope_freshness_and_fqn_scope_id():
    s = _snap(last_modified=_NOW - timedelta(hours=10))
    findings = evaluate(s, _NOW)
    assert all(f.scope == "freshness" for f in findings)
    assert all(f.scope_id == "raw.weather_hourly" for f in findings)


def test_findings_carry_owner_team_in_details():
    s = _snap(last_modified=_NOW - timedelta(hours=10))
    findings = evaluate(s, _NOW)
    assert all((f.details or {}).get("owner_team") == "data_owners" for f in findings)
