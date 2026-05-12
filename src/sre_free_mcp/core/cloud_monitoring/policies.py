"""Alert policy schema.

Customers declare GCP Cloud Monitoring alert policies in
``alert_policies.yaml`` rather than clicking through the console. The
sync routine in :mod:`sweep` reads these and creates/updates the
matching alert policies in the customer's project.

Schema is intentionally smaller than the full Monitoring API — covers
the common case (one metric, one threshold, one notification channel
group) and leaves the long tail (multi-condition policies, log-based
alerts, MQL) to be authored directly in the Monitoring console.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AlertCondition(BaseModel):
    """One metric-threshold condition."""

    model_config = ConfigDict(frozen=True)

    metric_type: str
    filter: str = ""                    # extra MQL/PromQL-style filter on resource labels
    comparison: Literal[
        "COMPARISON_GT", "COMPARISON_LT", "COMPARISON_GE", "COMPARISON_LE"
    ] = "COMPARISON_GT"
    threshold_value: float
    duration_seconds: int = 300          # how long the condition must hold
    aggregation_alignment_period_seconds: int = 60
    aggregation_per_series_aligner: Literal[
        "ALIGN_RATE", "ALIGN_MEAN", "ALIGN_MAX", "ALIGN_SUM", "ALIGN_PERCENTILE_95"
    ] = "ALIGN_MEAN"


class AlertPolicy(BaseModel):
    """One alert policy declaration."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    documentation: str = ""              # surfaces in PagerDuty / Slack payload
    severity: Literal["low", "medium", "high", "critical"] = "high"
    enabled: bool = True
    owner_team: str

    # Resource-side targeting (which workflows / Cloud Run jobs this
    # policy covers). Translated to monitoring filter at apply time.
    workflow_name: str | None = None     # tag to thread through to gap_reports

    conditions: list[AlertCondition]

    # Notification channel group keys (matched against recipients.yaml
    # groups for cross-check during config load — channels themselves
    # are created out-of-band).
    notification_groups: list[str] = Field(default_factory=list)

    @field_validator("display_name", "owner_team")
    @classmethod
    def _non_empty_string(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("conditions")
    @classmethod
    def _at_least_one_condition(cls, v: list[AlertCondition]) -> list[AlertCondition]:
        if not v:
            raise ValueError("AlertPolicy requires at least one condition")
        return v


class AlertPoliciesConfig(BaseModel):
    """The alert_policies.yaml file content."""

    policies: list[AlertPolicy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_display_names(self) -> "AlertPoliciesConfig":
        seen: set[str] = set()
        for p in self.policies:
            if p.display_name in seen:
                raise ValueError(
                    f"duplicate alert policy display_name {p.display_name!r} — "
                    "display_name uniquely identifies a policy"
                )
            seen.add(p.display_name)
        return self


__all__ = ["AlertCondition", "AlertPoliciesConfig", "AlertPolicy"]
