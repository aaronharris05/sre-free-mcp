"""Unit tests for the retry circuit breaker.

Stubs the BQ client; verifies state transitions (closed → open →
half_open) using the same aggregate the live implementation reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.retry.breaker import state_for
from sre_free_mcp.core.retry.policy import RetryPolicy

_NOW = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
_DEFAULT_POLICY = RetryPolicy()  # threshold=5, window=60min


class _FakeBQ:
    """Returns canned (total, recent) tuple from queries."""

    def __init__(self, *, total: int, recent: int) -> None:
        self._total = total
        self._recent = recent

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        return _FakeJob(self._total, self._recent)


class _FakeJob:
    def __init__(self, total: int, recent: int) -> None:
        self._total = total
        self._recent = recent

    def result(self) -> list[dict[str, int]]:
        return [{"total": self._total, "recent": self._recent}]


def test_breaker_closed_below_threshold():
    bq = _FakeBQ(total=2, recent=2)
    state = state_for("wf_a", bq_client=bq, project="p", now=_NOW, policy=_DEFAULT_POLICY)
    assert state == "closed"


def test_breaker_open_at_threshold_with_recent_failures():
    bq = _FakeBQ(total=5, recent=3)  # at threshold + recent activity
    state = state_for("wf_a", bq_client=bq, project="p", now=_NOW, policy=_DEFAULT_POLICY)
    assert state == "open"


def test_breaker_open_above_threshold():
    bq = _FakeBQ(total=8, recent=4)
    state = state_for("wf_a", bq_client=bq, project="p", now=_NOW, policy=_DEFAULT_POLICY)
    assert state == "open"


def test_breaker_half_open_after_quiesce_window():
    """At/above threshold but no failures in the last 30 min → half_open
    (one trial allowed)."""
    bq = _FakeBQ(total=5, recent=0)
    state = state_for("wf_a", bq_client=bq, project="p", now=_NOW, policy=_DEFAULT_POLICY)
    assert state == "half_open"


def test_breaker_fails_open_on_bq_error():
    """If BQ throws, the breaker returns 'closed' so transient BQ blips
    don't stall the fleet."""

    class _RaisingBQ:
        def query(self, sql: str, job_config: Any | None = None) -> Any:
            raise RuntimeError("simulated BQ outage")

    state = state_for(
        "wf_a",
        bq_client=_RaisingBQ(),
        project="p",
        now=_NOW,
        policy=_DEFAULT_POLICY,
    )
    assert state == "closed"


def test_breaker_threshold_respects_policy_override():
    """A tighter policy (threshold=2) should open at counts that are
    fine under the default policy (threshold=5)."""
    tight = RetryPolicy(breaker_threshold=2, breaker_window_min=60)
    bq = _FakeBQ(total=3, recent=2)
    state = state_for("wf_a", bq_client=bq, project="p", now=_NOW, policy=tight)
    assert state == "open"


def test_breaker_honors_custom_events_table_name():
    """Customers who rename the governance dataset thread the override
    through `events_table=`. Verify the SQL the breaker generates uses
    the override (we check by capturing the SQL into a sentinel BQ)."""

    captured: dict[str, str] = {}

    class _CapturingBQ:
        def query(self, sql: str, job_config: Any | None = None) -> _FakeJob:
            captured["sql"] = sql
            return _FakeJob(0, 0)

    state_for(
        "wf_a",
        bq_client=_CapturingBQ(),
        project="my-proj",
        now=_NOW,
        policy=_DEFAULT_POLICY,
        events_table="sre.events",
    )
    assert "`my-proj.sre.events`" in captured["sql"]
