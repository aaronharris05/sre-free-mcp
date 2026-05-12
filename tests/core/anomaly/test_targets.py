"""Unit tests for the anomaly target schema + helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_free_mcp.core.anomaly.targets import (
    AnomalyTarget,
    AnomalyTargetsConfig,
    active_targets,
    find,
)


def _t(
    table: str = "ex.weather",
    metric: str = "temp_f",
    owner: str = "data_owners",
    not_yet_built: bool = False,
    severity_red_z: float = 5.0,
    severity_yellow_z: float = 3.5,
) -> AnomalyTarget:
    return AnomalyTarget(
        table=table,
        metric_column=metric,
        timestamp_column="ts",
        lookback_days=14,
        severity_red_z=severity_red_z,
        severity_yellow_z=severity_yellow_z,
        owner_team=owner,
        purpose="test target",
        not_yet_built=not_yet_built,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_target_is_frozen():
    t = _t()
    with pytest.raises(ValidationError):
        t.lookback_days = 99  # type: ignore[misc]


def test_target_rejects_red_below_yellow():
    with pytest.raises(ValidationError, match="severity_red_z"):
        _t(severity_red_z=3.0, severity_yellow_z=5.0)


def test_target_accepts_red_equal_yellow():
    """Equal is the boundary case — should be allowed (a single
    threshold for medium = high)."""
    t = _t(severity_red_z=4.0, severity_yellow_z=4.0)
    assert t.severity_red_z == t.severity_yellow_z


def test_target_rejects_zero_lookback_days():
    with pytest.raises(ValidationError):
        AnomalyTarget(
            table="ex.a",
            metric_column="m",
            timestamp_column="ts",
            lookback_days=0,
            severity_red_z=5.0,
            severity_yellow_z=3.0,
            owner_team="data_owners",
            purpose="x",
        )


def test_target_rejects_blank_table():
    with pytest.raises(ValidationError):
        _t(table="   ")


# ---------------------------------------------------------------------------
# AnomalyTargetsConfig — duplicate detection
# ---------------------------------------------------------------------------


def test_targets_config_rejects_duplicate_pairs():
    with pytest.raises(ValidationError, match="duplicate"):
        AnomalyTargetsConfig(targets=[_t(table="ex.a", metric="m"), _t(table="ex.a", metric="m")])


def test_targets_config_allows_same_table_different_metric():
    cfg = AnomalyTargetsConfig(
        targets=[
            _t(table="ex.a", metric="m1"),
            _t(table="ex.a", metric="m2"),
        ]
    )
    assert len(cfg.targets) == 2


def test_targets_config_defaults_to_empty():
    assert AnomalyTargetsConfig().targets == []


# ---------------------------------------------------------------------------
# active_targets + find
# ---------------------------------------------------------------------------


def test_active_targets_excludes_not_yet_built():
    targets = [
        _t(table="ex.a", metric="m1", not_yet_built=False),
        _t(table="ex.b", metric="m2", not_yet_built=True),
        _t(table="ex.c", metric="m3", not_yet_built=False),
    ]
    active = active_targets(targets)
    assert len(active) == 2
    assert {t.table for t in active} == {"ex.a", "ex.c"}


def test_active_targets_with_all_built_returns_full_list():
    targets = [_t(table="ex.a", metric="m1"), _t(table="ex.b", metric="m2")]
    assert active_targets(targets) == targets


def test_find_returns_matching_target():
    targets = [
        _t(table="ex.weather", metric="temp_f", owner="data_owners"),
        _t(table="ex.load", metric="mw", owner="trader_owners"),
    ]
    t = find(targets, "ex.load", "mw")
    assert t is not None
    assert t.owner_team == "trader_owners"


def test_find_returns_none_for_unknown_table():
    targets = [_t(table="ex.a", metric="m")]
    assert find(targets, "ex.does_not_exist", "m") is None


def test_find_returns_none_for_known_table_unknown_metric():
    """Routing keys on (table, metric), not just table."""
    targets = [_t(table="ex.a", metric="m1")]
    assert find(targets, "ex.a", "not_a_metric") is None


def test_find_with_empty_targets_returns_none():
    assert find([], "any", "any") is None
