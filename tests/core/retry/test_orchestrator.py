"""Smoke tests for the retry orchestrator.

Stubs every external (BQ + Cloud Run + breaker) and verifies the
decision branches: idempotency veto, breaker veto, backoff wait,
schedule, exhaust + RCA enqueue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.retry import orchestrator
from sre_free_mcp.core.retry.policy import clear_overrides
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_overrides():
    clear_overrides()
    yield
    clear_overrides()


class _FakeBQ:
    """In-memory BQ. Captures inserts so tests can assert what landed."""

    def __init__(self) -> None:
        self.project = "test-proj"
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def query(self, sql: str, job_config: Any | None = None) -> Any:
        # Tests patch the SQL helpers directly; query() should not be
        # reached from a properly-stubbed test.
        raise AssertionError("Test should patch SQL helpers directly; query() not stubbed")

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_external(
    monkeypatch,
    *,
    candidates: list[dict[str, Any]],
    idempotent: dict[str, bool],
    attempts: dict[str, tuple[int, datetime | None]],
    breaker: dict[str, str],
    trigger_returns: bool = True,
):
    monkeypatch.setattr(
        orchestrator, "_read_red_pipelines", lambda b, p, t: list(candidates)
    )
    monkeypatch.setattr(
        orchestrator, "_read_idempotent", lambda b, p, name, t: idempotent.get(name, False)
    )
    monkeypatch.setattr(
        orchestrator, "_read_attempts", lambda b, p, name, t: attempts.get(name, (0, None))
    )
    monkeypatch.setattr(
        orchestrator, "breaker_state", lambda name, **kw: breaker.get(name, "closed")
    )
    monkeypatch.setattr(orchestrator, "_resolve_job_name", lambda p, r, name: f"{name}-job")
    monkeypatch.setattr(orchestrator, "_trigger_execution", lambda p, r, j: trigger_returns)


def _tick(bq: _FakeBQ) -> orchestrator.TickResult:
    return orchestrator.tick(
        project="test-proj",
        region="us-central1",
        bq_client=bq,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# Idempotency veto
# ---------------------------------------------------------------------------


def test_non_idempotent_workflow_is_vetoed(monkeypatch):
    candidates = [
        {"workflow_name": "billing_close", "worst_gap_kind": "workflow_failing_persistently"}
    ]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"billing_close": False},
        attempts={},
        breaker={},
    )
    result = _tick(bq)
    assert result.by_outcome["not_idempotent"] == 1

    events = [r for tbl, rows in bq.inserts for r in rows if "events" in tbl]
    assert any('"outcome": "not_idempotent"' in r["payload"] for r in events)


# ---------------------------------------------------------------------------
# Circuit breaker veto
# ---------------------------------------------------------------------------


def test_open_breaker_vetoes_retry(monkeypatch):
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"}]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={},
        breaker={"wf_a": "open"},
    )
    result = _tick(bq)
    assert result.by_outcome["breaker_open"] == 1


# ---------------------------------------------------------------------------
# Backoff wait
# ---------------------------------------------------------------------------


def test_orchestrator_waits_when_backoff_not_satisfied(monkeypatch):
    """First retry uses 30s backoff (default policy); last attempt 5s ago
    doesn't satisfy that, so the orchestrator must wait."""
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"}]
    last_at = _NOW - timedelta(seconds=5)
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={"wf_a": (0, last_at)},
        breaker={},
    )
    result = _tick(bq)
    assert result.by_outcome.get("waiting") == 1


# ---------------------------------------------------------------------------
# Happy path: schedule a retry
# ---------------------------------------------------------------------------


def test_orchestrator_schedules_retry_when_eligible(monkeypatch):
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"}]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={"wf_a": (0, None)},
        breaker={"wf_a": "closed"},
    )
    result = _tick(bq)
    assert result.by_outcome["ok"] == 1

    events = [r for tbl, rows in bq.inserts for r in rows if "events" in tbl]
    assert any('"outcome": "ok"' in r["payload"] for r in events)
    assert any('"attempt_number": 1' in r["payload"] for r in events)


def test_orchestrator_records_trigger_failed_when_run_job_returns_false(monkeypatch):
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"}]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={"wf_a": (0, None)},
        breaker={"wf_a": "closed"},
        trigger_returns=False,
    )
    result = _tick(bq)
    assert result.by_outcome["trigger_failed"] == 1


# ---------------------------------------------------------------------------
# Exhausted: enqueue RCA item
# ---------------------------------------------------------------------------


def test_exhausted_enqueues_approval_queue_item(monkeypatch):
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_failing_persistently"}]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={"wf_a": (3, _NOW - timedelta(hours=1))},  # 3 prior → exhausted
        breaker={"wf_a": "closed"},
    )
    result = _tick(bq)
    assert result.by_outcome["exhausted"] == 1

    aq_inserts = [r for tbl, rows in bq.inserts for r in rows if "approval_queue" in tbl]
    assert len(aq_inserts) == 1
    assert aq_inserts[0]["action_kind"] == "retry_exhausted"
    assert aq_inserts[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# Mixed batch + structure
# ---------------------------------------------------------------------------


def test_mixed_outcomes_in_one_tick(monkeypatch):
    """One veto + one happy schedule in the same tick — counts should
    both land in by_outcome."""
    candidates = [
        {"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"},
        {"workflow_name": "wf_b", "worst_gap_kind": "workflow_failing_persistently"},
    ]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True, "wf_b": False},
        attempts={},
        breaker={},
    )
    result = _tick(bq)
    assert result.candidates == 2
    assert result.by_outcome["ok"] == 1
    assert result.by_outcome["not_idempotent"] == 1


def test_tick_with_no_candidates_returns_empty_result(monkeypatch):
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=[],
        idempotent={},
        attempts={},
        breaker={},
    )
    result = _tick(bq)
    assert result.candidates == 0
    assert result.by_outcome == {}


def test_per_workflow_exception_does_not_abort_tick(monkeypatch):
    """If processing one workflow raises, the orchestrator records an
    'error' decision and continues with the next candidate."""
    candidates = [
        {"workflow_name": "wf_boom", "worst_gap_kind": "workflow_missed_run"},
        {"workflow_name": "wf_ok", "worst_gap_kind": "workflow_missed_run"},
    ]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_boom": True, "wf_ok": True},
        attempts={"wf_ok": (0, None)},
        breaker={},
    )

    def _bad_attempts_lookup(b, p, name, t):
        if name == "wf_boom":
            raise RuntimeError("simulated BQ blip")
        return (0, None)

    monkeypatch.setattr(orchestrator, "_read_attempts", _bad_attempts_lookup)

    result = _tick(bq)
    assert result.candidates == 2
    assert result.by_outcome["error"] == 1
    assert result.by_outcome["ok"] == 1


# ---------------------------------------------------------------------------
# Required-arg validation
# ---------------------------------------------------------------------------


def test_tick_requires_project(monkeypatch):
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        orchestrator.tick(project="", region="us-central1", bq_client=bq, now=_NOW)


def test_tick_requires_region(monkeypatch):
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="region is required"):
        orchestrator.tick(project="p", region="", bq_client=bq, now=_NOW)


# ---------------------------------------------------------------------------
# GovernanceTables flow-through
# ---------------------------------------------------------------------------


def test_custom_governance_dataset_threads_into_event_writes(monkeypatch):
    """Customers who renamed the dataset should see events land in
    `{project}.{custom_dataset}.events`, not the default."""
    candidates = [{"workflow_name": "wf_a", "worst_gap_kind": "workflow_missed_run"}]
    bq = _FakeBQ()
    _patch_external(
        monkeypatch,
        candidates=candidates,
        idempotent={"wf_a": True},
        attempts={"wf_a": (0, None)},
        breaker={"wf_a": "closed"},
    )
    orchestrator.tick(
        project="test-proj",
        region="us-central1",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "test-proj.sre.events" for tbl, _ in bq.inserts)
