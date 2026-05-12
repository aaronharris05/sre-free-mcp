"""Pure-logic AI-governance rules over one Workflow row."""

from __future__ import annotations

from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.workflows import Workflow


def evaluate(
    workflow: Workflow,
    *,
    business_purpose_min_chars: int = 30,
    require_policy_refs: bool = False,
) -> list[Finding]:
    """Apply AI-governance rules to one workflow row."""
    findings: list[Finding] = []
    base = {"workflow": workflow.name}

    if not workflow.business_purpose:
        findings.append(
            Finding(
                scope="ai_gov",
                scope_id=workflow.name,
                gap_kind="missing_business_purpose",
                severity="high",
                details={
                    **base,
                    "rule": "policy: every workflow must document its "
                    "business_purpose",
                },
            )
        )
    elif len(workflow.business_purpose.strip()) < business_purpose_min_chars:
        findings.append(
            Finding(
                scope="ai_gov",
                scope_id=workflow.name,
                gap_kind="business_purpose_too_terse",
                severity="medium",
                details={
                    **base,
                    "business_purpose": workflow.business_purpose,
                    "min_chars": business_purpose_min_chars,
                    "actual_chars": len(workflow.business_purpose.strip()),
                    "rule": f"business_purpose < {business_purpose_min_chars} "
                    "chars; likely placeholder",
                },
            )
        )

    if require_policy_refs and not workflow.policy_refs:
        findings.append(
            Finding(
                scope="ai_gov",
                scope_id=workflow.name,
                gap_kind="missing_policy_refs",
                severity="medium",
                details={
                    **base,
                    "rule": "policy: workflow must reference at least one "
                    "policy ID (require_policy_refs=True)",
                },
            )
        )

    return findings


__all__ = ["evaluate"]
