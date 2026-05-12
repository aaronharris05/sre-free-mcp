"""Anomaly sweep — one daily pass over the configured target registry.

For each active target:

1. Read ``lookback_days`` of ``(timestamp_column, metric_column)``
   from BigQuery.
2. Fit the anomaly engine on the metric column.
3. Convert per-row scores to z-equivalents and flag rows past
   ``severity_red_z`` / ``severity_yellow_z``.
4. Build :class:`Finding` rows and write them to ``gap_reports``.

The sweep is **read-only** beyond the gap_reports write. Email
notification is a separate concern handled by the caller (the runner
groups findings by ``owner_team`` and ships one email per team).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.anomaly.engine import score as engine_score
from sre_free_mcp.core.anomaly.engine import z_equivalent
from sre_free_mcp.core.anomaly.targets import AnomalyTarget
from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one anomaly sweep.

    Attributes:
        scanned: number of targets actually scored (excludes skipped).
        skipped: number of targets skipped (``not_yet_built=True``).
        findings: every finding produced this run, across all targets.
        run_id: UUID4 for this sweep — useful for log correlation.
        generated_at: sweep timestamp.
    """

    scanned: int
    skipped: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    targets: list[AnomalyTarget],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write_findings: bool = True,
) -> SweepResult:
    """Run one anomaly sweep over ``targets``.

    Args:
        project: GCP project that owns the BQ tables.
        bq_client: a ``google.cloud.bigquery.Client`` (or a stub with
            the same ``.query()`` / ``.insert_rows_json()`` surface).
        targets: targets to scan. Caller filters out inactive ones
            (use :func:`sre_free_mcp.core.anomaly.targets.active_targets`).
        tables: governance dataset overrides; defaults to ``governance``.
        now: sweep timestamp. Defaults to ``datetime.now(UTC)``.
        write_findings: when False, the sweep computes findings but
            does NOT write them to ``gap_reports`` — useful for dry-runs
            and for the MCP tool when the caller wants to inspect
            findings without persisting them.

    Returns:
        :class:`SweepResult` with every finding produced.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    findings: list[Finding] = []
    skipped = 0
    scanned = 0

    for target in targets:
        if target.not_yet_built:
            logger.info(
                "anomaly_skip not_yet_built table=%s metric=%s",
                target.table,
                target.metric_column,
            )
            skipped += 1
            continue
        try:
            scanned += 1
            findings.extend(_run_one(target, bq_client=bq_client, project=project, now=now))
        except Exception:
            logger.exception(
                "anomaly_scan_failed table=%s metric=%s",
                target.table,
                target.metric_column,
            )

    if findings and write_findings:
        _write_findings(bq_client, findings, project=project, tables=tables, generated_at=now)

    logger.info(
        "anomaly_complete scanned=%d skipped=%d findings=%d",
        scanned,
        skipped,
        len(findings),
    )

    return SweepResult(
        scanned=scanned,
        skipped=skipped,
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Per-target scan
# ---------------------------------------------------------------------------


def _run_one(
    target: AnomalyTarget,
    *,
    bq_client: Any,
    project: str,
    now: datetime,
) -> list[Finding]:
    """Score one target's lookback window and return flagged rows."""
    rows = _read_window(bq_client, project, target, now)
    if not rows:
        return []

    values = [r["value"] for r in rows if r["value"] is not None]
    scores, model_name = engine_score(values)
    if not scores:
        logger.info(
            "anomaly_thin_window table=%s metric=%s n=%d",
            target.table,
            target.metric_column,
            len(values),
        )
        return []

    z_scores = z_equivalent(scores)

    findings: list[Finding] = []
    j = 0  # index into values/scores, skips Nones
    for r in rows:
        if r["value"] is None:
            continue
        z = z_scores[j]
        j += 1
        sev = _severity_for(z, target)
        if sev is None:
            continue
        findings.append(_build_finding(target, r, z=z, severity=sev, model=model_name))
    return findings


def _severity_for(z: float, target: AnomalyTarget) -> str | None:
    if abs(z) >= target.severity_red_z:
        return "high"
    if abs(z) >= target.severity_yellow_z:
        return "medium"
    return None


def _read_window(
    bq_client: Any,
    project: str,
    target: AnomalyTarget,
    now: datetime,
) -> list[dict[str, Any]]:
    """Read the lookback window for one target.

    Returns rows shaped ``{"ts": <isoformat>, "value": <float|None>}``.
    """
    table_ref = f"{project}.{target.table}"
    sql = f"""
        SELECT
          {target.timestamp_column} AS ts,
          {target.metric_column} AS value
        FROM `{table_ref}`
        WHERE {target.timestamp_column} >= TIMESTAMP_SUB(
          @now, INTERVAL @days DAY
        )
        ORDER BY ts ASC
    """
    try:
        from google.cloud import bigquery  # lazy

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
                bigquery.ScalarQueryParameter("days", "INT64", target.lookback_days),
            ]
        )
        rows: list[dict[str, Any]] = []
        for r in bq_client.query(sql, job_config=job_config).result():
            ts = r["ts"]
            val = r["value"]
            rows.append(
                {
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "value": float(val) if val is not None else None,
                }
            )
        return rows
    except Exception:
        logger.exception(
            "anomaly_read_window_failed table=%s metric=%s",
            target.table,
            target.metric_column,
        )
        return []


def _build_finding(
    target: AnomalyTarget,
    row: dict[str, Any],
    *,
    z: float,
    severity: str,
    model: str,
) -> Finding:
    return Finding(
        scope="data",
        scope_id=f"{target.table}:{target.metric_column}",
        gap_kind="anomaly_zscore",
        severity=severity,
        details={
            "table": target.table,
            "metric_column": target.metric_column,
            "z_score": z,
            "value": row["value"],
            "as_of": row["ts"],
            "owner_team": target.owner_team,
            "model": model,
            "purpose": target.purpose,
        },
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _write_findings(
    bq_client: Any,
    findings: list[Finding],
    *,
    project: str,
    tables: GovernanceTables,
    generated_at: datetime,
) -> None:
    """Module-local wrapper around :func:`core.findings.write_findings`
    so tests can still monkeypatch this name."""
    _write_findings_shared(
        bq_client,
        findings,
        project=project,
        generated_at=generated_at,
        tables=tables,
        source="sre_free_mcp.core.anomaly.detector",
    )


__all__ = ["SweepResult", "sweep"]
