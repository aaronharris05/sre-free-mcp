"""Pure-logic uptime checks. No I/O.

Given a :class:`WorkflowStatus` (status + recent execution history),
:func:`evaluate` applies five rules and returns the corresponding
:class:`Finding` rows.

Rules and gap_kinds:
  - ``workflow_paused`` — Scheduler trigger is paused.
  - ``workflow_missed_run`` — last execution older than (cadence + grace).
  - ``workflow_failing_persistently`` — last N executions all failed.
  - ``workflow_no_scheduler`` — registered with trigger_kind=scheduler
    but no Cloud Scheduler resource matches.
  - ``workflow_stale_registration`` — workflow registered but never
    executed (likely dead code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sre_free_mcp.core.models import Finding

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStatus:
    """Uptime evidence for one workflow row.

    Populated by :mod:`audit` from the workflows registry + Cloud Run
    + Cloud Scheduler APIs.
    """

    name: str
    cron: str | None
    trigger_kind: str | None
    job_name: str | None
    last_execution_at: datetime | None
    last_execution_status: str | None       # 'ok' | 'failed' | None
    last_n_statuses: list[str] = field(default_factory=list)
    scheduler_paused: bool | None = None    # None when no scheduler resource


def evaluate(
    status: WorkflowStatus,
    now: datetime,
    *,
    grace_hours: float = 2.0,
    failing_streak: int = 3,
) -> list[Finding]:
    """Apply the rule set to one workflow row."""
    findings: list[Finding] = []
    base_details: dict = {"workflow": status.name, "cron": status.cron}

    # 1. Paused scheduler
    if status.scheduler_paused is True:
        findings.append(
            Finding(
                scope="workflow",
                scope_id=status.name,
                gap_kind="workflow_paused",
                severity="high",
                details={**base_details, "rule": "scheduler.state=paused"},
            )
        )

    # 2. Missed run
    if status.cron and status.last_execution_at is not None:
        cadence = cron_to_timedelta(status.cron)
        if cadence:
            tolerance = cadence + timedelta(hours=grace_hours)
            age = now - status.last_execution_at
            if age > tolerance:
                findings.append(
                    Finding(
                        scope="workflow",
                        scope_id=status.name,
                        gap_kind="workflow_missed_run",
                        severity="high",
                        details={
                            **base_details,
                            "expected_cadence_hours": cadence.total_seconds() / 3600,
                            "last_execution_at": status.last_execution_at.isoformat(),
                            "age_hours": age.total_seconds() / 3600,
                        },
                    )
                )

    # 3. Persistent failures
    failing = status.last_n_statuses[:failing_streak]
    if len(failing) >= failing_streak and all(s == "failed" for s in failing):
        findings.append(
            Finding(
                scope="workflow",
                scope_id=status.name,
                gap_kind="workflow_failing_persistently",
                severity="critical",
                details={
                    **base_details,
                    "failing_streak": failing_streak,
                    "recent_statuses": status.last_n_statuses,
                },
            )
        )

    # 4. No scheduler when one is expected
    if (
        status.trigger_kind == "scheduler"
        and status.job_name
        and status.scheduler_paused is None
    ):
        findings.append(
            Finding(
                scope="workflow",
                scope_id=status.name,
                gap_kind="workflow_no_scheduler",
                severity="high",
                details={
                    **base_details,
                    "expected_scheduler": f"{status.job_name}-trigger",
                    "rule": "trigger_kind=scheduler but no Cloud Scheduler resource found",
                },
            )
        )

    # 5. Stale registration
    # Only fires when we've literally never seen the workflow execute
    # and it has a cron (so it's expected to run on a schedule). Missed-
    # cycle cases are covered by workflow_missed_run above.
    if status.last_execution_at is None and status.cron is not None:
        findings.append(
            Finding(
                scope="workflow",
                scope_id=status.name,
                gap_kind="workflow_stale_registration",
                severity="medium",
                details={
                    **base_details,
                    "rule": "scheduled workflow registered but never executed",
                },
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Cron parsing — minimal, supports the patterns we actually use today
# ---------------------------------------------------------------------------


def cron_to_timedelta(cron: str) -> timedelta | None:
    """Map common cron patterns to expected period.

    Returns None for unrecognized patterns so the missed-run rule fails
    open (no false-positives on exotic schedules). Supports:

    * ``*/N * * * *`` — every N minutes
    * ``M H * * *``   — daily at fixed time
    * ``M H * * D``   — weekly on day D at fixed time
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts

    # Every-N-minutes shorthand
    if minute.startswith("*/") and minute[2:].isdigit() and hour == "*":
        return timedelta(minutes=int(minute[2:]))

    if minute == "*" and hour == "*":
        return timedelta(minutes=1)

    # Hourly: any-minute, *, *, *
    if minute.isdigit() and hour == "*":
        return timedelta(hours=1)

    # Weekly: minute, hour, *, *, specific-day
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*" and dow.isdigit():
        return timedelta(days=7)

    # Daily: minute, hour, *, *, *
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*" and dow == "*":
        return timedelta(days=1)

    return None


__all__ = ["WorkflowStatus", "cron_to_timedelta", "evaluate"]
