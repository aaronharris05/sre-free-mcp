"""Pure-logic cost-anomaly rule. No I/O.

A row from ``governance.cost_daily`` carries today's spend +
``baseline_avg_28d`` + ``z_score`` (already computed by the ETL). This
module's job is to map ``(z, spend)`` to a severity, or no finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from sre_free_mcp.core.models import Finding


@dataclass
class CostSnapshot:
    """One ``cost_daily`` row's worth of evidence."""

    usage_date: str           # ISO date
    service: str
    workflow_name: str | None
    cost_usd: float
    baseline_avg_28d: float
    z_score: float


def evaluate(
    snapshot: CostSnapshot,
    *,
    z_yellow: float = 3.0,
    z_red: float = 5.0,
    min_spend_usd: float = 1.0,
    min_baseline_usd: float = 0.50,
) -> Finding | None:
    """Return a finding if the row is anomalous, else None.

    Three guards keep the rule from firing on noise:

    * ``min_spend_usd`` — a $0.04→$0.20 jump is technically 5x but
      operationally meaningless. Default $1.
    * ``min_baseline_usd`` — a brand-new service with a near-zero
      baseline would otherwise look like an infinite-x spike on day 1.
    * ``z_score`` thresholds — yellow at 3σ, red at 5σ.

    Returns ``None`` when no rule fires.
    """
    if snapshot.cost_usd < min_spend_usd:
        return None
    if snapshot.baseline_avg_28d < min_baseline_usd:
        return None

    abs_z = abs(snapshot.z_score)
    if abs_z >= z_red:
        severity = "critical"
    elif abs_z >= z_yellow:
        severity = "high"
    else:
        return None

    scope_id = (
        f"{snapshot.service}:{snapshot.workflow_name}"
        if snapshot.workflow_name
        else snapshot.service
    )
    spike_ratio = (
        snapshot.cost_usd / snapshot.baseline_avg_28d
        if snapshot.baseline_avg_28d > 0
        else None
    )
    return Finding(
        scope="cost",
        scope_id=scope_id,
        gap_kind="cost_anomaly",
        severity=severity,
        details={
            "as_of": snapshot.usage_date,
            "service": snapshot.service,
            "workflow_name": snapshot.workflow_name,
            "cost_usd": snapshot.cost_usd,
            "baseline_avg_28d": snapshot.baseline_avg_28d,
            "z_score": snapshot.z_score,
            "spike_ratio": spike_ratio,
            "rule": f"|z| >= {z_yellow} (yellow) / {z_red} (red); cost >= ${min_spend_usd}",
        },
    )


__all__ = ["CostSnapshot", "evaluate"]
