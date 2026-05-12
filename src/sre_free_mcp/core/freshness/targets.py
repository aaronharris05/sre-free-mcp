"""Freshness-audit target schema.

Each :class:`FreshnessTarget` declares one BigQuery table along with
its expected refresh cadence. The freshness audit checks every active
target against the table's actual ``last_modified_time`` from
``INFORMATION_SCHEMA``.

Targets are loaded from ``freshness_targets.yaml`` by the config layer.
Tables not in the registry are not audited — explicit opt-in, no
implicit "every table in dataset X has SLA Y" rules.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FreshnessTarget(BaseModel):
    """One table + its freshness SLA.

    Attributes:
        dataset: BigQuery dataset (no project prefix; the audit
            resolves project at runtime).
        table: BigQuery table name (no dataset prefix).
        expected_cadence_hours: how often the table is expected to be
            updated. Used by the rule engine to compute staleness
            thresholds (2x → high, 5x → critical).
        owner_team: recipients group the finding routes to.
        purpose: short human description for the email body.
        not_yet_built: True when the table doesn't exist yet; the
            audit skips it with a log line.
    """

    model_config = ConfigDict(frozen=True)

    dataset: str
    table: str
    expected_cadence_hours: float = Field(gt=0)
    owner_team: str
    purpose: str = ""
    not_yet_built: bool = False

    @field_validator("dataset", "table", "owner_team")
    @classmethod
    def _non_empty_string(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @property
    def fqn(self) -> str:
        """Dataset-qualified table name."""
        return f"{self.dataset}.{self.table}"


class FreshnessTargetsConfig(BaseModel):
    """The freshness_targets.yaml file content."""

    targets: list[FreshnessTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_tables(self) -> "FreshnessTargetsConfig":
        seen: set[tuple[str, str]] = set()
        for t in self.targets:
            key = (t.dataset, t.table)
            if key in seen:
                raise ValueError(
                    f"duplicate freshness target for ({t.dataset!r}, {t.table!r}) — "
                    "each (dataset, table) pair must appear at most once"
                )
            seen.add(key)
        return self


def active_targets(targets: list[FreshnessTarget]) -> list[FreshnessTarget]:
    return [t for t in targets if not t.not_yet_built]


def find(
    targets: list[FreshnessTarget], dataset: str, table: str
) -> FreshnessTarget | None:
    for t in targets:
        if t.dataset == dataset and t.table == table:
            return t
    return None


__all__ = [
    "FreshnessTarget",
    "FreshnessTargetsConfig",
    "active_targets",
    "find",
]
