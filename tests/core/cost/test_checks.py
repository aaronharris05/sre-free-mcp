"""Unit tests for the cost-anomaly rule logic."""

from __future__ import annotations

from sre_free_mcp.core.cost.checks import CostSnapshot, evaluate


def _snap(
    *,
    service: str = "Cloud Run",
    workflow: str | None = "wf_a",
    cost_usd: float = 100.0,
    baseline: float = 10.0,
    z_score: float = 6.0,
    usage_date: str = "2026-05-12",
) -> CostSnapshot:
    return CostSnapshot(
        usage_date=usage_date,
        service=service,
        workflow_name=workflow,
        cost_usd=cost_usd,
        baseline_avg_28d=baseline,
        z_score=z_score,
    )


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


def test_high_severity_at_3_sigma():
    f = evaluate(_snap(z_score=3.5))
    assert f is not None
    assert f.severity == "high"


def test_critical_severity_at_5_sigma():
    f = evaluate(_snap(z_score=5.5))
    assert f is not None
    assert f.severity == "critical"


def test_no_finding_below_yellow_threshold():
    assert evaluate(_snap(z_score=2.0)) is None


def test_negative_z_score_triggered_on_absolute_magnitude():
    """Spend dropping anomalously low is just as interesting as a spike."""
    f = evaluate(_snap(z_score=-6.0))
    assert f is not None
    assert f.severity == "critical"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_min_spend_floor_suppresses_tiny_spikes():
    """A $0.50 charge with z=10 is noise, not a finding."""
    assert evaluate(_snap(cost_usd=0.50, baseline=0.05)) is None


def test_min_baseline_floor_suppresses_brand_new_resources():
    """A new service with $0.05/day baseline ramping to $5 is first-day
    usage, not an anomaly."""
    assert evaluate(_snap(cost_usd=5.0, baseline=0.05)) is None


def test_custom_z_thresholds():
    """Tighter thresholds catch a 4σ spike that the default tolerates."""
    s = _snap(z_score=4.0)
    assert evaluate(s) is not None  # >= default 3.0 → high
    # Tighter threshold: yellow=4.5 means 4.0 doesn't fire.
    assert evaluate(s, z_yellow=4.5, z_red=6.0) is None


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_scope_id_includes_workflow_when_present():
    f = evaluate(_snap(service="Cloud Run", workflow="wf_a"))
    assert f is not None
    assert f.scope_id == "Cloud Run:wf_a"


def test_scope_id_falls_back_to_service_when_workflow_null():
    f = evaluate(_snap(service="BigQuery", workflow=None))
    assert f is not None
    assert f.scope_id == "BigQuery"


def test_details_include_baseline_and_ratio():
    f = evaluate(_snap(cost_usd=100, baseline=10, z_score=6.0))
    assert f is not None
    d = f.details
    assert d["cost_usd"] == 100
    assert d["baseline_avg_28d"] == 10
    assert d["spike_ratio"] == 10.0
    assert d["as_of"] == "2026-05-12"


def test_spike_ratio_handles_zero_baseline_safely():
    """If baseline is exactly 0 the ratio is None, not infinite."""
    # Need baseline >= min_baseline_usd to get past the guard; we'll
    # pass min_baseline_usd=0 to verify spike_ratio gracefully handles
    # baseline=0.
    f = evaluate(
        _snap(cost_usd=100, baseline=0.0, z_score=6.0),
        min_baseline_usd=0.0,
    )
    assert f is not None
    assert f.details["spike_ratio"] is None
