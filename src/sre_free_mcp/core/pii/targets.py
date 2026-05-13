"""PII-inspection target schema.

Each :class:`PiiTarget` is one BigQuery table the DLP sentinel scans.
Customers list these in ``pii_targets.yaml``; the audit reads them at
runtime.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PiiTarget(BaseModel):
    """One BigQuery table flagged for DLP inspection."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    table: str
    sample_rows: int = Field(default=1000, gt=0)
    info_types: list[str] = Field(
        default_factory=lambda: [
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SOCIAL_SECURITY_NUMBER",
            "CREDIT_CARD_NUMBER",
            "PERSON_NAME",
        ]
    )
    not_yet_built: bool = False

    @field_validator("dataset", "table")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @property
    def fqn(self) -> str:
        return f"{self.dataset}.{self.table}"


class PiiTargetsConfig(BaseModel):
    """The pii_targets.yaml file content."""

    targets: list[PiiTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "PiiTargetsConfig":
        seen: set[tuple[str, str]] = set()
        for t in self.targets:
            key = (t.dataset, t.table)
            if key in seen:
                raise ValueError(
                    f"duplicate pii target ({t.dataset!r}, {t.table!r})"
                )
            seen.add(key)
        return self


__all__ = ["PiiTarget", "PiiTargetsConfig"]
