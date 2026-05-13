"""SCC findings forwarder.

Reads ``ACTIVE`` SCC findings via the SecurityCenter API, maps SCC
severity → ours, and writes one row per finding to gap_reports.

SCC's data model is busy (finding + source + asset references, plus
external_uri). We project the subset the operator actually reads in
an email: category, severity, resource_name, event_time, plus the
external_uri to drill back into the SCC console.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)


_SCC_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "SEVERITY_UNSPECIFIED": "low",
}


@dataclass(frozen=True)
class SccFinding:
    """Trimmed mirror of a SecurityCenter Finding."""

    name: str                # /findings/<id> resource name
    category: str            # 'PUBLIC_BUCKET', 'OPEN_FIREWALL', …
    severity: str            # 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | …
    resource_name: str
    event_time: datetime
    external_uri: str | None
    state: str               # 'ACTIVE' | 'INACTIVE' | …


@dataclass(frozen=True)
class AuditResult:
    active_findings: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    organization_id: str = "",
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
) -> AuditResult:
    """One security-findings sweep.

    Args:
        organization_id: GCP organization the SCC findings live under.
            When empty (or SCC client unavailable) the sweep no-ops.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    if not organization_id:
        logger.info("security: no organization_id configured; skipping SCC fetch")
        return AuditResult(
            active_findings=0,
            findings=[],
            run_id=run_id,
            generated_at=now,
        )

    scc_findings = list_active_scc_findings(organization_id)
    findings: list[Finding] = [_to_finding(f) for f in scc_findings]

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.security",
        )

    logger.info("security_complete active_scc=%d findings=%d", len(scc_findings), len(findings))
    return AuditResult(
        active_findings=len(scc_findings),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


def _to_finding(scc: SccFinding) -> Finding:
    return Finding(
        scope="security",
        scope_id=scc.resource_name or scc.name,
        gap_kind=scc.category.lower() if scc.category else "scc_finding",
        severity=_SCC_SEVERITY_MAP.get(scc.severity.upper(), "medium"),
        details={
            "scc_name": scc.name,
            "category": scc.category,
            "severity_raw": scc.severity,
            "resource_name": scc.resource_name,
            "event_time": scc.event_time.isoformat(),
            "external_uri": scc.external_uri,
            "state": scc.state,
        },
    )


# ---------------------------------------------------------------------------
# GCP-facing helpers — stubbed in tests
# ---------------------------------------------------------------------------


def list_active_scc_findings(organization_id: str) -> list[SccFinding]:
    """Pull every ACTIVE SCC finding under ``organization_id``.

    Returns ``[]`` if the SCC client isn't installed or the API rejects
    the call (e.g., SCC not enabled on the org).
    """
    try:
        from google.cloud import securitycenter_v1  # lazy
    except Exception:
        logger.info("security: google-cloud-securitycenter not installed; skipping")
        return []
    try:
        client = securitycenter_v1.SecurityCenterClient()
        parent = f"organizations/{organization_id}/sources/-"
        request = securitycenter_v1.ListFindingsRequest(
            parent=parent,
            filter="state=\"ACTIVE\"",
        )
        out: list[SccFinding] = []
        for result in client.list_findings(request=request):
            f = result.finding
            severity_name = getattr(f.severity, "name", str(f.severity))
            state_name = getattr(f.state, "name", str(f.state))
            event_time = getattr(f, "event_time", None)
            if event_time is None:
                event_time = datetime.now(UTC)
            elif hasattr(event_time, "ToDatetime"):
                event_time = event_time.ToDatetime().replace(tzinfo=UTC)
            out.append(
                SccFinding(
                    name=f.name,
                    category=f.category,
                    severity=severity_name,
                    resource_name=f.resource_name,
                    event_time=event_time,
                    external_uri=getattr(f, "external_uri", None) or None,
                    state=state_name,
                )
            )
        return out
    except Exception:
        logger.exception("list_active_scc_findings failed (non-fatal)")
        return []


__all__ = ["AuditResult", "SccFinding", "list_active_scc_findings", "sweep"]
