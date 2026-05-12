"""Cost anomaly sweep.

Queries the pre-aggregated ``governance.cost_daily`` table for the
most recent ``usage_date``, runs :func:`evaluate` on each row, and
writes findings with scope='cost'.

The aggregation itself (pulling from billing_export, computing the
28-day baseline, writing cost_daily) is handled by a separate ETL —
the Terraform module installs it as a BigQuery scheduled query. This
audit only reads the already-aggregated table.
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

from .checks import CostSnapshot, evaluate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostRow:
    """Mirror of one row from ``governance.cost_daily``."""

    usage_date: str
    service: str
    workflow_name: str | None
    cost_usd: float
    baseline_avg_28d: float
    z_score: float


@dataclass(frozen=True)
class AuditResult:
    scanned: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
    z_yellow: float = 3.0,
    z_red: float = 5.0,
    min_spend_usd: float = 1.0,
    min_baseline_usd: float = 0.50,
) -> AuditResult:
    """Run one cost-anomaly sweep against the most recent
    ``cost_daily`` partition.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    rows = read_rows(bq_client, project, tables=tables)
    findings: list[Finding] = []
    for r in rows:
        snap = CostSnapshot(
            usage_date=r.usage_date,
            service=r.service,
            workflow_name=r.workflow_name,
            cost_usd=r.cost_usd,
            baseline_avg_28d=r.baseline_avg_28d,
            z_score=r.z_score,
        )
        f = evaluate(
            snap,
            z_yellow=z_yellow,
            z_red=z_red,
            min_spend_usd=min_spend_usd,
            min_baseline_usd=min_baseline_usd,
        )
        if f is not None:
            findings.append(f)

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.cost",
        )

    logger.info("cost_anomaly_complete scanned=%d findings=%d", len(rows), len(findings))
    return AuditResult(
        scanned=len(rows),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


def read_rows(
    bq_client: Any,
    project: str,
    *,
    tables: GovernanceTables | None = None,
) -> list[CostRow]:
    """Read the most recent ``cost_daily`` partition."""
    tables = tables or GovernanceTables()
    sql = f"""
    WITH latest AS (
      SELECT MAX(usage_date) AS d FROM `{project}.{tables.cost_daily}`
    )
    SELECT
      CAST(c.usage_date AS STRING) AS usage_date,
      c.service,
      c.workflow_name,
      c.cost_usd,
      c.baseline_avg_28d,
      c.z_score
    FROM `{project}.{tables.cost_daily}` c, latest
    WHERE c.usage_date = latest.d
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("cost_daily read failed (non-fatal)")
        return []
    return [
        CostRow(
            usage_date=r["usage_date"],
            service=r["service"],
            workflow_name=r["workflow_name"],
            cost_usd=float(r["cost_usd"] or 0),
            baseline_avg_28d=float(r["baseline_avg_28d"] or 0),
            z_score=float(r["z_score"] or 0),
        )
        for r in rows
    ]


__all__ = ["AuditResult", "CostRow", "read_rows", "sweep"]
