"""YAML-driven configuration for sre-free-mcp.

Three files in the customer's `config/` directory drive everything:

* ``install.yaml``        — project ID, region, dataset name, email + LLM settings
* ``retry_policies.yaml`` — per-workflow retry overrides
* ``recipients.yaml``     — team-group → email-address mapping

Both the MCP server and the runner call :func:`load_config` at startup
and then :func:`apply_retry_overrides` so :func:`sre_free_mcp.core.retry.policy.lookup`
returns the customer's overrides.

Schema is validated with pydantic; bad config crashes at startup with a
useful error rather than producing wrong behavior at 04:00 UTC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from sre_free_mcp.core.anomaly.targets import AnomalyTargetsConfig
from sre_free_mcp.core.freshness.targets import FreshnessTargetsConfig


# ---------------------------------------------------------------------------
# install.yaml
# ---------------------------------------------------------------------------


class EmailConfig(BaseModel):
    """SMTP settings the email sender uses."""

    from_address: str
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password_secret: str = Field(
        description="Secret Manager secret name (not value) for the SMTP password."
    )


class LLMConfig(BaseModel):
    """LLM provider settings. ``provider='none'`` disables narrative generation."""

    provider: Literal["gemini", "anthropic", "openai", "none"] = "none"
    model: str = ""
    api_key_secret: str = ""


class InstallConfig(BaseModel):
    """Top-level install settings — one per GCP project deployment."""

    project_id: str
    region: str = "us-central1"
    governance_dataset: str = "governance"
    email: EmailConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)


# ---------------------------------------------------------------------------
# retry_policies.yaml
# ---------------------------------------------------------------------------


class RetryPolicyEntry(BaseModel):
    """One row in retry_policies.yaml — maps a workflow name to its policy."""

    workflow_name: str
    max_attempts: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [30, 300, 1800])
    breaker_window_min: int = 60
    breaker_threshold: int = 5
    require_idempotent: bool = True

    @field_validator("max_attempts")
    @classmethod
    def _max_attempts_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_attempts must be >= 1")
        return v


class RetryPoliciesConfig(BaseModel):
    """The retry_policies.yaml file content."""

    policies: list[RetryPolicyEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# recipients.yaml
# ---------------------------------------------------------------------------


class RecipientsConfig(BaseModel):
    """team-group → email-address mapping."""

    groups: dict[str, list[str]] = Field(default_factory=dict)

    def lookup(self, group: str) -> list[str]:
        """Return the list of email addresses for ``group``.

        Raises KeyError with a helpful message if the group is not
        defined — surfaces typos at the call site rather than silently
        emailing nobody.
        """
        if group not in self.groups:
            available = ", ".join(sorted(self.groups)) or "(none)"
            raise KeyError(
                f"recipients.yaml has no group {group!r}; available groups: {available}"
            )
        return list(self.groups[group])


# ---------------------------------------------------------------------------
# Top-level config + loader
# ---------------------------------------------------------------------------


class Config(BaseModel):
    """Parsed view of every YAML file."""

    install: InstallConfig
    retry_policies: RetryPoliciesConfig = Field(default_factory=RetryPoliciesConfig)
    recipients: RecipientsConfig = Field(default_factory=RecipientsConfig)
    anomaly_targets: AnomalyTargetsConfig = Field(default_factory=AnomalyTargetsConfig)
    freshness_targets: FreshnessTargetsConfig = Field(
        default_factory=FreshnessTargetsConfig
    )

    @model_validator(mode="after")
    def _targets_route_to_known_teams(self) -> "Config":
        """Every audit target's owner_team must exist in recipients.yaml.

        A target routed to an undefined team would fall through to the
        fallback at email time — better to surface the typo at config-
        load instead. Skipped when no recipients are configured at all
        (fresh install with only install.yaml).
        """
        if not self.recipients.groups:
            return self
        known_teams = set(self.recipients.groups)
        for t in self.anomaly_targets.targets:
            if t.owner_team not in known_teams:
                available = ", ".join(sorted(known_teams))
                raise ValueError(
                    f"anomaly target {t.table!r}.{t.metric_column!r} routes to "
                    f"owner_team {t.owner_team!r} but that group is not defined "
                    f"in recipients.yaml; available groups: {available}"
                )
        for t in self.freshness_targets.targets:
            if t.owner_team not in known_teams:
                available = ", ".join(sorted(known_teams))
                raise ValueError(
                    f"freshness target {t.dataset!r}.{t.table!r} routes to "
                    f"owner_team {t.owner_team!r} but that group is not defined "
                    f"in recipients.yaml; available groups: {available}"
                )
        return self


def load_config(config_dir: Path | str) -> Config:
    """Load and validate all three YAML files from ``config_dir``.

    ``install.yaml`` is required; the other two default to empty when
    absent (a fresh install has no overrides and no recipient groups
    yet, and that should still boot).
    """
    config_dir = Path(config_dir)
    if not config_dir.is_dir():
        raise FileNotFoundError(f"config_dir does not exist: {config_dir}")

    install_path = config_dir / "install.yaml"
    if not install_path.is_file():
        raise FileNotFoundError(f"install.yaml not found in {config_dir}")

    install_data = _load_yaml(install_path)
    retry_data = _load_yaml(config_dir / "retry_policies.yaml", default={})
    recipients_data = _load_yaml(config_dir / "recipients.yaml", default={})
    anomaly_data = _load_yaml(config_dir / "anomaly_targets.yaml", default={})
    freshness_data = _load_yaml(config_dir / "freshness_targets.yaml", default={})

    return Config(
        install=InstallConfig(**install_data),
        retry_policies=RetryPoliciesConfig(**retry_data),
        recipients=RecipientsConfig(**recipients_data),
        anomaly_targets=AnomalyTargetsConfig(**anomaly_data),
        freshness_targets=FreshnessTargetsConfig(**freshness_data),
    )


def apply_retry_overrides(config: Config) -> int:
    """Register every retry-policy entry with the global retry registry.

    Returns the number of overrides applied. Called once at startup by
    both the MCP server and the runner.
    """
    # Local import to avoid pulling retry module into config import path.
    from sre_free_mcp.core.retry.policy import RetryPolicy, register_override

    n = 0
    for entry in config.retry_policies.policies:
        register_override(
            entry.workflow_name,
            RetryPolicy(
                max_attempts=entry.max_attempts,
                backoff_seconds=tuple(entry.backoff_seconds),
                breaker_window_min=entry.breaker_window_min,
                breaker_threshold=entry.breaker_threshold,
                require_idempotent=entry.require_idempotent,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_yaml(path: Path, default: Any = None) -> Any:
    """Load one YAML file. Missing files return ``default`` when given."""
    if not path.is_file():
        if default is not None:
            return default
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(data).__name__}")
    return data


__all__ = [
    "AnomalyTargetsConfig",
    "Config",
    "EmailConfig",
    "FreshnessTargetsConfig",
    "InstallConfig",
    "LLMConfig",
    "RecipientsConfig",
    "RetryPoliciesConfig",
    "RetryPolicyEntry",
    "apply_retry_overrides",
    "load_config",
]
