"""Unit tests for retry-policy lookup + backoff curve."""

from __future__ import annotations

import pytest

from sre_free_mcp.core.retry.policy import (
    RetryPolicy,
    clear_overrides,
    lookup,
    next_backoff_seconds,
    register_override,
)


@pytest.fixture(autouse=True)
def _reset_overrides():
    """Each test starts with an empty override registry."""
    clear_overrides()
    yield
    clear_overrides()


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def test_lookup_default_for_unknown_workflow():
    p = lookup("some_arbitrary_unregistered_workflow")
    assert p.max_attempts == 3
    assert p.backoff_seconds == (30, 300, 1800)
    assert p.require_idempotent is True


def test_lookup_returns_registered_override():
    register_override(
        "ingest_quota_limited",
        RetryPolicy(max_attempts=2, backoff_seconds=(60, 600)),
    )
    p = lookup("ingest_quota_limited")
    assert p.max_attempts == 2
    assert p.backoff_seconds == (60, 600)


def test_register_override_replaces_existing():
    register_override("wf_a", RetryPolicy(max_attempts=2))
    register_override("wf_a", RetryPolicy(max_attempts=5))
    assert lookup("wf_a").max_attempts == 5


def test_clear_overrides_drops_all_registrations():
    register_override("wf_a", RetryPolicy(max_attempts=2))
    clear_overrides()
    # back to defaults
    assert lookup("wf_a").max_attempts == 3


# ---------------------------------------------------------------------------
# next_backoff_seconds
# ---------------------------------------------------------------------------


def test_next_backoff_returns_none_when_exhausted():
    p = RetryPolicy(max_attempts=3, backoff_seconds=(30, 300, 1800))
    assert next_backoff_seconds(p, prior_attempts=3) is None
    assert next_backoff_seconds(p, prior_attempts=4) is None


@pytest.mark.parametrize(
    "prior,expected",
    [
        (1, 30),  # first retry uses backoff_seconds[0]
        (2, 300),  # second retry → [1]
    ],
)
def test_next_backoff_default_curve(prior, expected):
    p = RetryPolicy(max_attempts=3, backoff_seconds=(30, 300, 1800))
    assert next_backoff_seconds(p, prior_attempts=prior) == expected


def test_next_backoff_clamps_when_curve_shorter_than_attempts():
    """If backoff_seconds is shorter than max_attempts-1, the last value
    is re-used for the tail attempts."""
    p = RetryPolicy(max_attempts=5, backoff_seconds=(60, 600))
    assert next_backoff_seconds(p, prior_attempts=1) == 60
    assert next_backoff_seconds(p, prior_attempts=2) == 600
    assert next_backoff_seconds(p, prior_attempts=3) == 600  # clamped tail
    assert next_backoff_seconds(p, prior_attempts=4) == 600
    assert next_backoff_seconds(p, prior_attempts=5) is None  # exhausted


def test_next_backoff_empty_curve_with_single_attempt():
    """A policy with max_attempts=1 and empty backoff_seconds: no retry
    is ever scheduled, so the first call is already exhausted."""
    p = RetryPolicy(max_attempts=1, backoff_seconds=())
    assert next_backoff_seconds(p, prior_attempts=1) is None
