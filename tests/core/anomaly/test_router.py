"""Unit tests for anomaly-finding owner routing."""

from __future__ import annotations

from sre_free_mcp.core.anomaly.router import owner_for
from sre_free_mcp.core.anomaly.targets import AnomalyTarget


def _t(table: str, metric: str, owner: str) -> AnomalyTarget:
    return AnomalyTarget(
        table=table,
        metric_column=metric,
        timestamp_column="ts",
        lookback_days=14,
        severity_red_z=5.0,
        severity_yellow_z=3.5,
        owner_team=owner,
        purpose="test",
    )


def test_each_target_routes_to_its_team():
    """The router must agree with the registry — no silent overrides."""
    targets = [
        _t("ex.api_latency", "latency_p95_ms", "data_owners"),
        _t("ex.load", "mw", "trader_owners"),
        _t("ex.ar_balance", "balance", "finance_owners"),
    ]
    for t in targets:
        assert owner_for(targets, t.table, t.metric_column) == t.owner_team


def test_unknown_table_falls_through_to_default_fallback():
    """An unregistered scope reaches governance_owners by default."""
    targets = [_t("ex.a", "m", "data_owners")]
    assert owner_for(targets, "ex.never_registered", "v") == "governance_owners"


def test_known_table_unknown_metric_falls_through():
    """Routing keys on (table, metric_column), not just table."""
    targets = [_t("ex.api_latency", "latency_p95_ms", "data_owners")]
    assert owner_for(targets, "ex.api_latency", "not_a_metric") == "governance_owners"


def test_fallback_team_is_customizable():
    """A customer who renamed governance_owners can pass their own."""
    targets = [_t("ex.a", "m", "data_owners")]
    assert owner_for(targets, "ex.unknown", "v", fallback="ops") == "ops"


def test_owner_for_with_empty_targets_returns_fallback():
    assert owner_for([], "ex.a", "m") == "governance_owners"
