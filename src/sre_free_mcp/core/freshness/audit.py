"""Freshness audit — sweep orchestrator.

For each configured :class:`FreshnessTarget`, query
``INFORMATION_SCHEMA.TABLE_STORAGE`` (or the legacy ``__TABLES__``) for
``last_modified_time`` + ``row_count``, apply :func:`evaluate`, and
write findings to ``gap_reports`` (scope='freshness').
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import TableFreshness, evaluate
from .targets import FreshnessTarget, active_targets

logger = logging.getLogger(__name__)

# BQ identifiers are alphanumeric + underscore. Reject anything else
# before interpolating into SQL — INFORMATION_SCHEMA paths cannot be
# parameterized so we validate at the call site.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AuditResult:
    scanned: int
    skipped: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    targets: list[FreshnessTarget],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
    stale_yellow_multiplier: float = 2.0,
    stale_red_multiplier: float = 5.0,
    empty_min_hours: float = 24.0,
) -> AuditResult:
    """Run one freshness sweep over ``targets``."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    findings: list[Finding] = []
    scanned = 0
    skipped = 0

    for target in active_targets(targets):
        try:
            snapshot = read_snapshot(bq_client, project, target)
            scanned += 1
        except Exception:
            logger.exception("freshness_read_failed %s", target.fqn)
            continue
        if snapshot is None:
            # Table not found / read failed — log already emitted.
            continue
        findings.extend(
            evaluate(
                snapshot,
                now,
                stale_yellow_multiplier=stale_yellow_multiplier,
                stale_red_multiplier=stale_red_multiplier,
                empty_min_hours=empty_min_hours,
            )
        )

    for target in targets:
        if target.not_yet_built:
            skipped += 1

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.freshness",
        )

    logger.info(
        "freshness_complete scanned=%d skipped=%d findings=%d",
        scanned,
        skipped,
        len(findings),
    )
    return AuditResult(
        scanned=scanned,
        skipped=skipped,
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Snapshot loader
# ---------------------------------------------------------------------------


def read_snapshot(
    bq_client: Any, project: str, target: FreshnessTarget
) -> TableFreshness | None:
    """Read one table's freshness snapshot from INFORMATION_SCHEMA.

    Raises :class:`ValueError` if any identifier (project, dataset,
    table) fails the safe-identifier check — INFORMATION_SCHEMA paths
    cannot be parameterized, so we validate before interpolation.
    """
    for v, kind in (
        (project, "project"),
        (target.dataset, "dataset"),
        (target.table, "table"),
    ):
        if not _SAFE_IDENT.match(v):
            raise ValueError(f"unsafe BigQuery identifier ({kind}): {v!r}")

    # INFORMATION_SCHEMA.TABLE_STORAGE includes last_modified_time +
    # total_rows. Filtering by table_name returns at most one row.
    sql = f"""
    SELECT
      MAX(storage_last_modified_time) AS last_modified,
      MAX(total_rows) AS row_count
    FROM `{project}.{target.dataset}.INFORMATION_SCHEMA.TABLE_STORAGE`
    WHERE table_name = @table
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("table", "STRING", target.table)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("freshness_snapshot_read_failed %s", target.fqn)
        return None
    if not rows or rows[0]["last_modified"] is None:
        return None
    return TableFreshness(
        dataset=target.dataset,
        table=target.table,
        last_modified=rows[0]["last_modified"],
        row_count=int(rows[0]["row_count"] or 0),
        expected_cadence_hours=target.expected_cadence_hours,
        owner_team=target.owner_team,
        purpose=target.purpose,
    )


__all__ = ["AuditResult", "read_snapshot", "sweep"]
