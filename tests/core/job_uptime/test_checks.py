"""Unit tests for the job_uptime rule logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sre_free_mcp.core.job_uptime.checks import (
    WorkflowStatus,
    cron_to_timedelta,
    evaluate,
)

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _status(
    *,
    name: str = "wf_a",
    cron: str | None = "0 4 * * *",
    trigger_kind: str | None = "scheduler",
    job_name: str | None = "wf-a-job",
    last_at: datetime | None = None,
    last_status: str | None = None,
    last_n: list[str] | None = None,
    paused: bool | None = None,
) -> WorkflowStatus:
    return WorkflowStatus(
        name=name,
        cron=cron,
        trigger_kind=trigger_kind,
        job_name=job_name,
        last_execution_at=last_at,
        last_execution_status=last_status,
        last_n_statuses=last_n or [],
        scheduler_paused=paused,
    )


# ---------------------------------------------------------------------------
# Rule 1: paused
# ---------------------------------------------------------------------------


def test_paused_scheduler_fires_workflow_paused():
    s = _status(paused=True, last_at=_NOW - timedelta(minutes=5), last_n=["ok"])
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_paused" in kinds


def test_unpaused_scheduler_does_not_fire_paused():
    s = _status(paused=False, last_at=_NOW - timedelta(minutes=5), last_n=["ok"])
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_paused" not in kinds


# ---------------------------------------------------------------------------
# Rule 2: missed run
# ---------------------------------------------------------------------------


def test_missed_run_fires_when_age_exceeds_cadence_plus_grace():
    """Daily cron + 2h grace = 26h tolerance. Last run 30h ago → missed."""
    s = _status(
        cron="0 4 * * *",
        last_at=_NOW - timedelta(hours=30),
        paused=False,
        last_n=["ok"],
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_missed_run" in kinds


def test_missed_run_does_not_fire_within_tolerance():
    s = _status(
        cron="0 4 * * *",
        last_at=_NOW - timedelta(hours=12),
        paused=False,
        last_n=["ok"],
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_missed_run" not in kinds


def test_missed_run_with_exotic_cron_fails_open():
    """Cron we can't parse → no missed-run finding (no false-positive)."""
    s = _status(
        cron="*/15 9-17 * * 1-5",  # weekdays during business hours
        last_at=_NOW - timedelta(days=30),
        paused=False,
        last_n=["ok"],
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_missed_run" not in kinds


def test_missed_run_uses_custom_grace_hours():
    """Tight grace catches a near-miss the default would tolerate."""
    s = _status(
        cron="0 * * * *",  # hourly
        last_at=_NOW - timedelta(hours=2, minutes=30),
        paused=False,
        last_n=["ok"],
    )
    # default 2h grace → 1h + 2h = 3h tolerance → 2h30 NOT past tolerance
    assert "workflow_missed_run" not in {f.gap_kind for f in evaluate(s, _NOW)}
    # tighter 1h grace → 1h + 1h = 2h tolerance → 2h30 IS past tolerance
    assert "workflow_missed_run" in {
        f.gap_kind for f in evaluate(s, _NOW, grace_hours=1.0)
    }


# ---------------------------------------------------------------------------
# Rule 3: persistent failures
# ---------------------------------------------------------------------------


def test_failing_persistently_fires_after_3_failures():
    s = _status(
        last_at=_NOW - timedelta(minutes=5),
        last_n=["failed", "failed", "failed"],
        paused=False,
    )
    findings = evaluate(s, _NOW)
    failing = [f for f in findings if f.gap_kind == "workflow_failing_persistently"]
    assert len(failing) == 1
    assert failing[0].severity == "critical"


def test_failing_persistently_does_not_fire_on_mixed_recent_statuses():
    s = _status(
        last_at=_NOW - timedelta(minutes=5),
        last_n=["failed", "ok", "failed"],
        paused=False,
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_failing_persistently" not in kinds


def test_failing_persistently_honors_custom_streak():
    """A workflow with 2 failures hits a streak=2 threshold but not the default 3."""
    s = _status(
        last_at=_NOW - timedelta(minutes=5),
        last_n=["failed", "failed"],
        paused=False,
    )
    assert "workflow_failing_persistently" not in {
        f.gap_kind for f in evaluate(s, _NOW)
    }
    assert "workflow_failing_persistently" in {
        f.gap_kind for f in evaluate(s, _NOW, failing_streak=2)
    }


# ---------------------------------------------------------------------------
# Rule 4: no scheduler
# ---------------------------------------------------------------------------


def test_no_scheduler_fires_when_scheduler_resource_missing():
    """trigger_kind=scheduler + job_name set + paused=None means we
    looked for the trigger and didn't find it."""
    s = _status(
        trigger_kind="scheduler",
        job_name="wf-a-job",
        last_at=_NOW - timedelta(minutes=5),
        last_n=["ok"],
        paused=None,
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_no_scheduler" in kinds


def test_no_scheduler_does_not_fire_for_event_triggered_workflows():
    s = _status(
        trigger_kind="event",
        job_name="wf-a-job",
        last_at=_NOW - timedelta(minutes=5),
        last_n=["ok"],
        paused=None,
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_no_scheduler" not in kinds


# ---------------------------------------------------------------------------
# Rule 5: stale registration
# ---------------------------------------------------------------------------


def test_stale_registration_fires_when_never_executed():
    s = _status(
        cron="0 4 * * *",
        last_at=None,
        last_n=[],
        paused=False,
        trigger_kind="scheduler",
    )
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_stale_registration" in kinds


def test_stale_registration_does_not_fire_for_on_demand_workflow():
    """No cron + never-executed: trusted as on-demand, not stale."""
    s = _status(cron=None, last_at=None, last_n=[], paused=None, trigger_kind="manual")
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "workflow_stale_registration" not in kinds


# ---------------------------------------------------------------------------
# Healthy workflow → no findings
# ---------------------------------------------------------------------------


def test_healthy_workflow_produces_no_findings():
    s = _status(
        cron="0 4 * * *",
        last_at=_NOW - timedelta(hours=1),
        last_n=["ok", "ok", "ok"],
        paused=False,
        trigger_kind="scheduler",
        job_name="wf-a-job",
    )
    # We also need paused != None to skip rule 4. _status default = None;
    # set it to False to indicate "scheduler exists and is running".
    assert evaluate(s, _NOW) == []


# ---------------------------------------------------------------------------
# cron_to_timedelta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cron,expected",
    [
        ("*/5 * * * *", timedelta(minutes=5)),
        ("*/15 * * * *", timedelta(minutes=15)),
        ("0 * * * *", timedelta(hours=1)),
        ("0 4 * * *", timedelta(days=1)),
        ("30 9 * * *", timedelta(days=1)),
        ("0 5 * * 0", timedelta(days=7)),
    ],
)
def test_cron_to_timedelta_recognized_patterns(cron, expected):
    assert cron_to_timedelta(cron) == expected


@pytest.mark.parametrize(
    "cron",
    [
        "*/15 9-17 * * 1-5",   # business hours range
        "0 4 1 * *",            # monthly
        "garbage",
        "0",
    ],
)
def test_cron_to_timedelta_returns_none_for_exotic_patterns(cron):
    assert cron_to_timedelta(cron) is None
