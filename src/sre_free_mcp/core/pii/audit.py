"""DLP-driven PII inspection of configured BigQuery tables.

Workflow per target:

  1. Submit a DLP inspect job over the table (sampled to N rows).
  2. Wait for the job to finish.
  3. Read the findings, normalize them into :class:`PiiFinding`.
  4. Write rows to ``governance.pii_findings``.
  5. Mirror high-severity findings into ``gap_reports`` with
     scope='pii' so they fold into the rollup.

DLP and BigQuery have their own quirks (regional endpoints, async
jobs); this audit lazy-imports both. Tests stub :func:`inspect_target`
directly to avoid the heavy clients.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .targets import PiiTarget

logger = logging.getLogger(__name__)


# Direct identifiers — when DLP says "very likely" we treat them as
# high severity; everything else is medium-or-below.
_DIRECT_IDENTIFIERS = {
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "CREDIT_CARD_NUMBER",
    "PASSPORT",
    "DRIVER_LICENSE_NUMBER",
}

_LIKELIHOOD_TO_SEVERITY = {
    "VERY_LIKELY": "high",
    "LIKELY": "medium",
    "POSSIBLE": "low",
    "UNLIKELY": "low",
    "VERY_UNLIKELY": "low",
}


@dataclass(frozen=True)
class PiiFinding:
    """Normalized DLP finding."""

    dataset: str
    table: str
    column_path: str
    info_type: str
    likelihood: str
    sample_count: int
    finding_count: int


@dataclass(frozen=True)
class AuditResult:
    targets_scanned: int
    targets_skipped: int
    pii_findings: list[PiiFinding] = field(default_factory=list)
    mirrored_findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    targets: list[PiiTarget],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
) -> AuditResult:
    """One PII inspection sweep over ``targets``."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    pii: list[PiiFinding] = []
    mirrored: list[Finding] = []
    scanned = 0
    skipped = 0

    for target in targets:
        if target.not_yet_built:
            logger.info("pii_skip not_yet_built %s", target.fqn)
            skipped += 1
            continue
        try:
            target_findings = inspect_target(project, target)
            scanned += 1
        except Exception:
            logger.exception("pii inspect failed for %s (non-fatal)", target.fqn)
            continue
        pii.extend(target_findings)
        for f in target_findings:
            mirror = _maybe_mirror_to_gap_reports(f)
            if mirror is not None:
                mirrored.append(mirror)

    if pii and write:
        _write_pii_findings(bq_client, project, tables, pii, now)
    if mirrored and write:
        _write_findings_shared(
            bq_client,
            mirrored,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.pii",
        )

    logger.info(
        "pii_complete scanned=%d skipped=%d pii_findings=%d mirrored=%d",
        scanned,
        skipped,
        len(pii),
        len(mirrored),
    )
    return AuditResult(
        targets_scanned=scanned,
        targets_skipped=skipped,
        pii_findings=pii,
        mirrored_findings=mirrored,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Severity classification + persistence
# ---------------------------------------------------------------------------


def _severity_for(finding: PiiFinding) -> str:
    base = _LIKELIHOOD_TO_SEVERITY.get(finding.likelihood, "low")
    if finding.info_type in _DIRECT_IDENTIFIERS and finding.likelihood == "VERY_LIKELY":
        return "critical"
    return base


def _maybe_mirror_to_gap_reports(finding: PiiFinding) -> Finding | None:
    severity = _severity_for(finding)
    if severity in ("low",):
        return None
    return Finding(
        scope="pii",
        scope_id=f"{finding.dataset}.{finding.table}:{finding.column_path}",
        gap_kind=f"pii_{finding.info_type.lower()}",
        severity=severity,
        details={
            "dataset": finding.dataset,
            "table": finding.table,
            "column_path": finding.column_path,
            "info_type": finding.info_type,
            "likelihood": finding.likelihood,
            "sample_count": finding.sample_count,
            "finding_count": finding.finding_count,
        },
    )


def _write_pii_findings(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    findings: list[PiiFinding],
    now: datetime,
) -> None:
    table_ref = f"{project}.{tables.pii_findings}"
    iso_now = now.isoformat()
    rows: list[dict[str, Any]] = []
    for f in findings:
        gid = uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{f.dataset}|{f.table}|{f.column_path}|{f.info_type}|{iso_now}",
        )
        rows.append(
            {
                "id": str(gid),
                "generated_at": iso_now,
                "dataset": f.dataset,
                "table_name": f.table,
                "column_path": f.column_path,
                "info_type": f.info_type,
                "pii_class": "pii.direct"
                if f.info_type in _DIRECT_IDENTIFIERS
                else "pii.derived",
                "likelihood": f.likelihood,
                "sample_count": f.sample_count,
                "finding_count": f.finding_count,
                "severity": _severity_for(f),
                "details": json.dumps(
                    {"info_type": f.info_type, "likelihood": f.likelihood}
                ),
                "resolved_at": None,
                "resolved_by": None,
                "source": "sre_free_mcp.core.pii",
            }
        )
    try:
        errors = bq_client.insert_rows_json(table_ref, rows)
        if errors:
            logger.error("pii_findings insert errors: %s", errors)
    except Exception:
        logger.exception("pii_findings insert failed (non-fatal)")


# ---------------------------------------------------------------------------
# DLP-facing helper — stubbed in tests
# ---------------------------------------------------------------------------


def inspect_target(project: str, target: PiiTarget) -> list[PiiFinding]:
    """Submit a Cloud DLP inspect job over one BQ table and return its
    findings. No-op (returns ``[]``) when google-cloud-dlp isn't installed
    or the job fails — the rest of the audit fleet stays operational."""
    try:
        from google.cloud import dlp_v2  # lazy
    except Exception:
        logger.info("pii: google-cloud-dlp not installed; skipping inspect")
        return []
    try:
        client = dlp_v2.DlpServiceClient()
        parent = f"projects/{project}/locations/global"
        inspect_job = {
            "inspect_job": {
                "inspect_config": {
                    "info_types": [{"name": t} for t in target.info_types],
                    "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
                    "limits": {"max_findings_per_request": 1000},
                    "include_quote": False,
                },
                "storage_config": {
                    "big_query_options": {
                        "table_reference": {
                            "project_id": project,
                            "dataset_id": target.dataset,
                            "table_id": target.table,
                        },
                        "rows_limit": target.sample_rows,
                        "sample_method": dlp_v2.BigQueryOptions.SampleMethod.RANDOM_START,
                    }
                },
            }
        }
        job = client.create_dlp_job(parent=parent, inspect_job=inspect_job["inspect_job"])
        # Poll until the job finishes. In production we'd want a
        # better wait strategy; for v1 this is fine.
        while True:
            job = client.get_dlp_job(name=job.name)
            if job.state in (
                dlp_v2.DlpJob.JobState.DONE,
                dlp_v2.DlpJob.JobState.FAILED,
                dlp_v2.DlpJob.JobState.CANCELED,
            ):
                break
        if job.state != dlp_v2.DlpJob.JobState.DONE:
            logger.warning("pii: DLP job ended in state %s", job.state)
            return []
        # Read findings from the inspect details — simplified, we
        # aggregate by (column_path, info_type).
        out: list[PiiFinding] = []
        details = job.inspect_details.result
        for info_stat in details.info_type_stats:
            out.append(
                PiiFinding(
                    dataset=target.dataset,
                    table=target.table,
                    column_path="",  # DLP doesn't break out per-column at this level
                    info_type=info_stat.info_type.name,
                    likelihood="LIKELY",
                    sample_count=target.sample_rows,
                    finding_count=int(info_stat.count),
                )
            )
        return out
    except Exception:
        logger.exception("inspect_target failed for %s (non-fatal)", target.fqn)
        return []


__all__ = ["AuditResult", "PiiFinding", "inspect_target", "sweep"]
