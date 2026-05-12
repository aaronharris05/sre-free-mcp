"""Pure-logic data-catalog rules over one Workflow row."""

from __future__ import annotations

from sre_free_mcp.core.job_uptime.checks import cron_to_timedelta
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.workflows import Workflow


def evaluate(workflow: Workflow) -> list[Finding]:
    """Apply catalog-completeness rules to one workflow row."""
    findings: list[Finding] = []
    base = {"workflow": workflow.name}

    if not workflow.source_path:
        findings.append(
            Finding(
                scope="catalog",
                scope_id=workflow.name,
                gap_kind="missing_source_path",
                severity="medium",
                details={
                    **base,
                    "rule": "workflow registered without source_path; SCM "
                    "audit + RCA cannot link to source",
                },
            )
        )

    if not workflow.owner_team:
        findings.append(
            Finding(
                scope="catalog",
                scope_id=workflow.name,
                gap_kind="missing_owner_team",
                severity="high",
                details={
                    **base,
                    "rule": "workflow has no owner_team; alert emails "
                    "fall through to governance_owners fallback",
                },
            )
        )

    if workflow.idempotent is None:
        findings.append(
            Finding(
                scope="catalog",
                scope_id=workflow.name,
                gap_kind="missing_idempotent_flag",
                severity="medium",
                details={
                    **base,
                    "rule": "idempotent is NULL; retry orchestrator vetoes "
                    "every retry until explicitly set TRUE or FALSE",
                },
            )
        )

    if workflow.trigger_kind == "scheduler":
        if not workflow.cron:
            findings.append(
                Finding(
                    scope="catalog",
                    scope_id=workflow.name,
                    gap_kind="missing_cron_for_scheduler",
                    severity="high",
                    details={
                        **base,
                        "rule": "trigger_kind=scheduler requires cron; "
                        "job_uptime missed-run rule cannot evaluate without it",
                    },
                )
            )
        elif cron_to_timedelta(workflow.cron) is None:
            findings.append(
                Finding(
                    scope="catalog",
                    scope_id=workflow.name,
                    gap_kind="unparseable_cron",
                    severity="low",
                    details={
                        **base,
                        "cron": workflow.cron,
                        "rule": "cron present but not in a recognised pattern; "
                        "missed-run rule will skip this workflow",
                    },
                )
            )

    return findings


__all__ = ["evaluate"]
