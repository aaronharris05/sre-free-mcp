"""Unit tests for the slim anomaly engine."""

from __future__ import annotations

import random

import pytest

from sre_free_mcp.core.anomaly.engine import score, z_equivalent


def test_score_returns_insufficient_data_for_thin_window():
    scores, model = score([1.0, 2.0, 3.0])
    assert scores == []
    assert model == "insufficient_data"


def test_score_returns_isolation_forest_for_normal_window():
    """30+ normally-distributed values produce non-empty IF scores."""
    random.seed(42)
    values = [random.gauss(0, 1) for _ in range(100)]
    scores, model = score(values)
    assert model == "isolation_forest"
    assert len(scores) == 100


def test_score_flags_obvious_outlier():
    """Inject an obvious outlier and verify it gets the highest score."""
    random.seed(42)
    values = [random.gauss(0, 1) for _ in range(99)] + [50.0]
    scores, _ = score(values)
    assert len(scores) == 100
    # The injected outlier (last index) should be the most-anomalous.
    max_idx = max(range(len(scores)), key=lambda i: scores[i])
    assert max_idx == 99


# ---------------------------------------------------------------------------
# z_equivalent
# ---------------------------------------------------------------------------


def test_z_equivalent_empty_input():
    assert z_equivalent([]) == []


def test_z_equivalent_constant_input_returns_zeros():
    """std=0 → all values get z=0 (avoid div-by-zero)."""
    assert z_equivalent([1.0, 1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0, 0.0]


def test_z_equivalent_mean_and_unit_variance():
    """z-scores should have mean≈0 and unit variance."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    zs = z_equivalent(values)
    assert abs(sum(zs) / len(zs)) < 1e-9
    var = sum(z**2 for z in zs) / len(zs)
    assert abs(var - 1.0) < 1e-9


def test_z_equivalent_outlier_gets_high_magnitude():
    """An outlier in the input gets the largest |z|."""
    values = [0.0] * 99 + [100.0]
    zs = z_equivalent(values)
    max_idx = max(range(len(zs)), key=lambda i: abs(zs[i]))
    assert max_idx == 99
    assert abs(zs[99]) > 5.0
