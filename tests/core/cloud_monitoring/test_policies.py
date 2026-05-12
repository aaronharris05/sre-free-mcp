"""Unit tests for the alert-policy schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_free_mcp.core.cloud_monitoring.policies import (
    AlertCondition,
    AlertPoliciesConfig,
    AlertPolicy,
)


def _cond(**overrides):
    defaults = {
        "metric_type": "run.googleapis.com/job/completed_execution_count",
        "comparison": "COMPARISON_GT",
        "threshold_value": 0,
    }
    defaults.update(overrides)
    return AlertCondition(**defaults)


def _pol(**overrides):
    defaults = {
        "display_name": "test-policy",
        "owner_team": "sre_owners",
        "conditions": [_cond()],
    }
    defaults.update(overrides)
    return AlertPolicy(**defaults)


# ---------------------------------------------------------------------------
# AlertCondition
# ---------------------------------------------------------------------------


def test_condition_rejects_invalid_comparison():
    with pytest.raises(ValidationError):
        AlertCondition(
            metric_type="m",
            comparison="COMPARISON_BAD",  # type: ignore[arg-type]
            threshold_value=1,
        )


def test_condition_rejects_invalid_aligner():
    with pytest.raises(ValidationError):
        AlertCondition(
            metric_type="m",
            threshold_value=1,
            aggregation_per_series_aligner="ALIGN_BAD",  # type: ignore[arg-type]
        )


def test_condition_defaults_are_sensible():
    c = AlertCondition(metric_type="m", threshold_value=1)
    assert c.comparison == "COMPARISON_GT"
    assert c.duration_seconds == 300
    assert c.aggregation_per_series_aligner == "ALIGN_MEAN"


# ---------------------------------------------------------------------------
# AlertPolicy
# ---------------------------------------------------------------------------


def test_policy_requires_at_least_one_condition():
    with pytest.raises(ValidationError, match="at least one condition"):
        AlertPolicy(
            display_name="x", owner_team="sre_owners", conditions=[]
        )


def test_policy_rejects_blank_display_name():
    with pytest.raises(ValidationError):
        _pol(display_name="  ")


def test_policy_is_frozen():
    p = _pol()
    with pytest.raises(ValidationError):
        p.enabled = False  # type: ignore[misc]


def test_policy_defaults_severity_high_enabled_true():
    p = _pol()
    assert p.severity == "high"
    assert p.enabled is True


# ---------------------------------------------------------------------------
# AlertPoliciesConfig
# ---------------------------------------------------------------------------


def test_policies_config_rejects_duplicate_display_names():
    with pytest.raises(ValidationError, match="duplicate"):
        AlertPoliciesConfig(
            policies=[_pol(display_name="dup"), _pol(display_name="dup")]
        )


def test_policies_config_defaults_to_empty():
    assert AlertPoliciesConfig().policies == []
