"""Anomaly-detection target schema.

Each :class:`AnomalyTarget` describes one ``(table, metric_column)``
pair the anomaly bot scans on its daily sweep. Pure-Python; no I/O.

Targets are loaded from ``anomaly_targets.yaml`` by the config layer
(see :mod:`sre_free_mcp.config`) — this module just defines the schema
and helpers for looking up / filtering a target list.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnomalyTarget(BaseModel):
    """One anomaly-detection target.

    Attributes:
        table: dataset-qualified table name, e.g. ``"raw.weather_hourly"``.
            The detector resolves the project at runtime.
        metric_column: numeric column to score for anomalies.
        timestamp_column: TIMESTAMP / DATE / DATETIME column used to
            window the lookback and report ``as_of`` per finding.
        lookback_days: how far back to fit the detector.
        severity_red_z: |z| above this threshold flags severity=high.
        severity_yellow_z: |z| above this threshold flags
            severity=medium. Below: not flagged.
        owner_team: recipients.yaml group key the email routes to.
        purpose: one-liner describing the target — surfaces in the
            email body so the operator knows what to look at.
        not_yet_built: True when the underlying table doesn't exist
            yet. The detector skips these with a log line.
    """

    model_config = ConfigDict(frozen=True)

    table: str
    metric_column: str
    timestamp_column: str
    lookback_days: int = Field(gt=0)
    severity_red_z: float = Field(gt=0)
    severity_yellow_z: float = Field(gt=0)
    owner_team: str
    purpose: str
    not_yet_built: bool = False

    @field_validator("table", "metric_column", "timestamp_column", "owner_team", "purpose")
    @classmethod
    def _non_empty_string(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @model_validator(mode="after")
    def _severity_thresholds_ordered(self) -> "AnomalyTarget":
        if self.severity_red_z < self.severity_yellow_z:
            raise ValueError(
                f"severity_red_z ({self.severity_red_z}) must be >= "
                f"severity_yellow_z ({self.severity_yellow_z})"
            )
        return self


class AnomalyTargetsConfig(BaseModel):
    """The anomaly_targets.yaml file content."""

    targets: list[AnomalyTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_table_metric_pairs(self) -> "AnomalyTargetsConfig":
        seen: set[tuple[str, str]] = set()
        for t in self.targets:
            key = (t.table, t.metric_column)
            if key in seen:
                raise ValueError(
                    f"duplicate anomaly target for ({t.table!r}, {t.metric_column!r}) — "
                    "each (table, metric_column) pair must appear at most once"
                )
            seen.add(key)
        return self


# ---------------------------------------------------------------------------
# Helpers — pure functions over a target list
# ---------------------------------------------------------------------------


def active_targets(targets: list[AnomalyTarget]) -> list[AnomalyTarget]:
    """Return targets whose underlying table exists today."""
    return [t for t in targets if not t.not_yet_built]


def find(
    targets: list[AnomalyTarget],
    table: str,
    metric_column: str,
) -> AnomalyTarget | None:
    """Look up a target by ``(table, metric_column)``.

    Returns None if no matching target is registered. Routing keys on
    BOTH columns: the same table queried with an unregistered metric
    column should NOT silently inherit the table's other targets.
    """
    for t in targets:
        if t.table == table and t.metric_column == metric_column:
            return t
    return None


__all__ = [
    "AnomalyTarget",
    "AnomalyTargetsConfig",
    "active_targets",
    "find",
]
