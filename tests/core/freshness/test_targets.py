"""Unit tests for the freshness target schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_free_mcp.core.freshness.targets import (
    FreshnessTarget,
    FreshnessTargetsConfig,
    active_targets,
    find,
)


def _t(
    *,
    dataset: str = "raw",
    table: str = "weather_hourly",
    cadence: float = 1.5,
    owner: str = "data_owners",
    not_yet_built: bool = False,
) -> FreshnessTarget:
    return FreshnessTarget(
        dataset=dataset,
        table=table,
        expected_cadence_hours=cadence,
        owner_team=owner,
        purpose="test target",
        not_yet_built=not_yet_built,
    )


def test_target_is_frozen():
    t = _t()
    with pytest.raises(ValidationError):
        t.expected_cadence_hours = 99  # type: ignore[misc]


def test_target_rejects_zero_cadence():
    with pytest.raises(ValidationError):
        FreshnessTarget(
            dataset="raw",
            table="x",
            expected_cadence_hours=0,
            owner_team="data_owners",
        )


def test_target_rejects_blank_dataset():
    with pytest.raises(ValidationError):
        _t(dataset="   ")


def test_target_fqn_property():
    t = _t(dataset="raw", table="weather_hourly")
    assert t.fqn == "raw.weather_hourly"


def test_targets_config_rejects_duplicate_table_pairs():
    with pytest.raises(ValidationError, match="duplicate"):
        FreshnessTargetsConfig(
            targets=[_t(dataset="raw", table="x"), _t(dataset="raw", table="x")]
        )


def test_targets_config_allows_same_table_different_dataset():
    cfg = FreshnessTargetsConfig(
        targets=[_t(dataset="raw", table="x"), _t(dataset="curated", table="x")]
    )
    assert len(cfg.targets) == 2


def test_active_targets_excludes_not_yet_built():
    targets = [
        _t(dataset="raw", table="a"),
        _t(dataset="raw", table="b", not_yet_built=True),
    ]
    actives = active_targets(targets)
    assert {t.table for t in actives} == {"a"}


def test_find_matching_target():
    targets = [_t(dataset="raw", table="a"), _t(dataset="curated", table="b")]
    t = find(targets, "curated", "b")
    assert t is not None and t.dataset == "curated"


def test_find_returns_none_for_unknown():
    assert find([_t()], "ghost_dataset", "x") is None
