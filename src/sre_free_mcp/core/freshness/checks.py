"""Pure-logic freshness rules. No I/O.

Given a :class:`TableFreshness` snapshot and the table's SLA,
:func:`evaluate` applies three rules and returns the corresponding
findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sre_free_mcp.core.models import Finding


@dataclass
class TableFreshness:
    """One snapshot of a table's freshness state.

    Populated by :mod:`audit` from ``INFORMATION_SCHEMA.TABLES``.
    ``last_modified`` is None for never-written tables.
    """

    dataset: str
    table: str
    last_modified: datetime | None
    row_count: int
    expected_cadence_hours: float
    owner_team: str = "governance_owners"
    purpose: str = ""


def evaluate(
    snapshot: TableFreshness,
    now: datetime,
    *,
    stale_yellow_multiplier: float = 2.0,
    stale_red_multiplier: float = 5.0,
    empty_min_hours: float = 24.0,
) -> list[Finding]:
    """Apply freshness rules to one table snapshot."""
    findings: list[Finding] = []
    fqn = f"{snapshot.dataset}.{snapshot.table}"
    base = {
        "dataset": snapshot.dataset,
        "table": snapshot.table,
        "owner_team": snapshot.owner_team,
        "purpose": snapshot.purpose,
    }

    if snapshot.last_modified is None:
        # Never-written table — caller decides whether to flag. For now
        # we follow the original: do not flag (catches empty staging
        # tables harmlessly).
        return findings

    age_h = (now - snapshot.last_modified).total_seconds() / 3600
    cadence = snapshot.expected_cadence_hours

    if age_h > stale_red_multiplier * cadence:
        findings.append(
            Finding(
                scope="freshness",
                scope_id=fqn,
                gap_kind="table_stale",
                severity="critical",
                details={
                    **base,
                    "rule": f"age > {stale_red_multiplier}x expected cadence",
                    "last_modified": snapshot.last_modified.isoformat(),
                    "age_hours": age_h,
                    "expected_cadence_hours": cadence,
                },
            )
        )
    elif age_h > stale_yellow_multiplier * cadence:
        findings.append(
            Finding(
                scope="freshness",
                scope_id=fqn,
                gap_kind="table_stale",
                severity="high",
                details={
                    **base,
                    "rule": f"age > {stale_yellow_multiplier}x expected cadence",
                    "last_modified": snapshot.last_modified.isoformat(),
                    "age_hours": age_h,
                    "expected_cadence_hours": cadence,
                },
            )
        )

    if (
        snapshot.row_count == 0
        and (now - snapshot.last_modified) > timedelta(hours=empty_min_hours)
    ):
        findings.append(
            Finding(
                scope="freshness",
                scope_id=fqn,
                gap_kind="table_empty",
                severity="medium",
                details={
                    **base,
                    "rule": f"row_count=0 for >{empty_min_hours}h after last write",
                    "last_modified": snapshot.last_modified.isoformat(),
                },
            )
        )

    return findings


__all__ = ["TableFreshness", "evaluate"]
