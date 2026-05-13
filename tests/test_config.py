"""Unit tests for the YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sre_free_mcp.config import (
    AnomalyTargetsConfig,
    Config,
    EmailConfig,
    InstallConfig,
    LLMConfig,
    RecipientsConfig,
    RetryPoliciesConfig,
    RetryPolicyEntry,
    apply_retry_overrides,
    load_config,
)
from sre_free_mcp.core.anomaly.targets import AnomalyTarget
from sre_free_mcp.core.retry.policy import clear_overrides, lookup


@pytest.fixture(autouse=True)
def _reset_overrides():
    clear_overrides()
    yield
    clear_overrides()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_install_config_requires_project_id():
    with pytest.raises(ValidationError) as exc:
        InstallConfig.model_validate(
            {
                "email": {
                    "from_address": "a@b.com",
                    "smtp_host": "h",
                    "smtp_username": "u",
                    "smtp_password_secret": "s",
                }
            }
        )
    assert "project_id" in str(exc.value)


def test_install_config_defaults_region_and_dataset():
    cfg = InstallConfig.model_validate(
        {
            "project_id": "my-proj",
            "email": {
                "from_address": "a@b.com",
                "smtp_host": "h",
                "smtp_username": "u",
                "smtp_password_secret": "s",
            },
        }
    )
    assert cfg.region == "us-central1"
    assert cfg.governance_dataset == "governance"
    assert cfg.llm.provider == "none"


def test_llm_config_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        LLMConfig.model_validate({"provider": "magic_llm"})


def test_retry_policy_entry_rejects_zero_attempts():
    with pytest.raises(ValidationError):
        RetryPolicyEntry.model_validate({"workflow_name": "x", "max_attempts": 0})


def test_retry_policy_entry_defaults():
    entry = RetryPolicyEntry.model_validate({"workflow_name": "x"})
    assert entry.max_attempts == 3
    assert entry.backoff_seconds == [30, 300, 1800]
    assert entry.require_idempotent is True


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def test_recipients_lookup_returns_list_copy():
    cfg = RecipientsConfig.model_validate({"groups": {"data": ["a@b.com", "c@d.com"]}})
    out = cfg.lookup("data")
    out.append("mutated@example.com")
    # Original groups should be unchanged — lookup returns a copy.
    assert cfg.lookup("data") == ["a@b.com", "c@d.com"]


def test_recipients_lookup_raises_with_available_groups_listed():
    cfg = RecipientsConfig.model_validate(
        {"groups": {"data_owners": ["a@b.com"], "trader_owners": ["t@b.com"]}}
    )
    with pytest.raises(KeyError) as exc:
        cfg.lookup("nope_owners")
    msg = str(exc.value)
    assert "nope_owners" in msg
    assert "data_owners" in msg
    assert "trader_owners" in msg


def test_recipients_lookup_with_empty_config_reports_none():
    cfg = RecipientsConfig()
    with pytest.raises(KeyError) as exc:
        cfg.lookup("anything")
    assert "(none)" in str(exc.value)


# ---------------------------------------------------------------------------
# load_config (file I/O)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_config_with_all_three_files(tmp_path: Path):
    _write(
        tmp_path / "install.yaml",
        """
project_id: test-proj
region: us-east1
email:
  from_address: sre@example.com
  smtp_host: smtp.example.com
  smtp_port: 465
  smtp_username: sre
  smtp_password_secret: sre-smtp
""",
    )
    _write(
        tmp_path / "retry_policies.yaml",
        """
policies:
  - workflow_name: my_workflow
    max_attempts: 2
    backoff_seconds: [60, 600]
""",
    )
    _write(
        tmp_path / "recipients.yaml",
        """
groups:
  data_owners:
    - data@example.com
""",
    )

    cfg = load_config(tmp_path)
    assert cfg.install.project_id == "test-proj"
    assert cfg.install.region == "us-east1"
    assert cfg.install.email.smtp_port == 465
    assert len(cfg.retry_policies.policies) == 1
    assert cfg.retry_policies.policies[0].workflow_name == "my_workflow"
    assert cfg.recipients.lookup("data_owners") == ["data@example.com"]


def test_load_config_missing_install_yaml_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError) as exc:
        load_config(tmp_path)
    assert "install.yaml" in str(exc.value)


def test_load_config_missing_config_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist")


def test_load_config_retry_and_recipients_default_empty(tmp_path: Path):
    """A fresh install with only install.yaml should still load."""
    _write(
        tmp_path / "install.yaml",
        """
project_id: test-proj
email:
  from_address: sre@example.com
  smtp_host: smtp.example.com
  smtp_username: sre
  smtp_password_secret: sre-smtp
""",
    )
    cfg = load_config(tmp_path)
    assert cfg.retry_policies.policies == []
    assert cfg.recipients.groups == {}


def test_load_config_rejects_top_level_list(tmp_path: Path):
    """install.yaml must be a mapping, not a list."""
    _write(tmp_path / "install.yaml", "- this\n- is\n- a list\n")
    with pytest.raises(ValueError, match="top-level YAML must be a mapping"):
        load_config(tmp_path)


# ---------------------------------------------------------------------------
# apply_retry_overrides
# ---------------------------------------------------------------------------


def _minimal_install() -> InstallConfig:
    return InstallConfig(
        project_id="p",
        email=EmailConfig(
            from_address="a@b.com",
            smtp_host="h",
            smtp_username="u",
            smtp_password_secret="s",
        ),
    )


def test_apply_retry_overrides_registers_each_entry():
    cfg = Config(
        install=_minimal_install(),
        retry_policies=RetryPoliciesConfig(
            policies=[
                RetryPolicyEntry(
                    workflow_name="wf_a",
                    max_attempts=2,
                    backoff_seconds=[60, 600],
                ),
                RetryPolicyEntry(workflow_name="wf_b", max_attempts=5),
            ]
        ),
    )
    n = apply_retry_overrides(cfg)
    assert n == 2
    assert lookup("wf_a").max_attempts == 2
    assert lookup("wf_a").backoff_seconds == (60, 600)
    assert lookup("wf_b").max_attempts == 5


def test_apply_retry_overrides_with_no_policies_is_noop():
    cfg = Config(install=_minimal_install())
    assert apply_retry_overrides(cfg) == 0
    # Unknown workflow still returns the default.
    assert lookup("anything").max_attempts == 3


def _example_target(owner: str = "data_owners") -> AnomalyTarget:
    return AnomalyTarget(
        table="ex.api_latency",
        metric_column="latency_p95_ms",
        timestamp_column="ts",
        lookback_days=14,
        severity_red_z=5.0,
        severity_yellow_z=3.5,
        owner_team=owner,
        purpose="test",
    )


# ---------------------------------------------------------------------------
# Cross-validator: anomaly targets must route to known recipient groups
# ---------------------------------------------------------------------------


def test_config_rejects_target_routing_to_unknown_team():
    """A target's owner_team must exist in recipients.yaml."""
    with pytest.raises(ValidationError, match="not defined in recipients.yaml"):
        Config(
            install=_minimal_install(),
            recipients=RecipientsConfig(groups={"data_owners": ["d@x.com"]}),
            anomaly_targets=AnomalyTargetsConfig(targets=[_example_target(owner="ghosts")]),
        )


def test_config_accepts_target_when_team_is_known():
    cfg = Config(
        install=_minimal_install(),
        recipients=RecipientsConfig(groups={"data_owners": ["d@x.com"]}),
        anomaly_targets=AnomalyTargetsConfig(targets=[_example_target(owner="data_owners")]),
    )
    assert len(cfg.anomaly_targets.targets) == 1


def test_config_skips_validation_when_recipients_empty():
    """A fresh install with no recipients yet should still load (the
    cross-validator only fires once recipients are populated)."""
    cfg = Config(
        install=_minimal_install(),
        anomaly_targets=AnomalyTargetsConfig(targets=[_example_target(owner="anything")]),
    )
    assert len(cfg.anomaly_targets.targets) == 1


def test_load_config_includes_anomaly_targets(tmp_path: Path):
    _write(
        tmp_path / "install.yaml",
        """
project_id: test-proj
email:
  from_address: sre@example.com
  smtp_host: smtp.example.com
  smtp_username: sre
  smtp_password_secret: sre-smtp
""",
    )
    _write(
        tmp_path / "recipients.yaml",
        """
groups:
  data_owners:
    - data@example.com
""",
    )
    _write(
        tmp_path / "anomaly_targets.yaml",
        """
targets:
  - table: ex.api_latency
    metric_column: latency_p95_ms
    timestamp_column: ts
    lookback_days: 14
    severity_red_z: 5.0
    severity_yellow_z: 3.5
    owner_team: data_owners
    purpose: test
""",
    )
    cfg = load_config(tmp_path)
    assert len(cfg.anomaly_targets.targets) == 1
    assert cfg.anomaly_targets.targets[0].owner_team == "data_owners"


def test_apply_retry_overrides_passes_through_breaker_settings():
    cfg = Config(
        install=_minimal_install(),
        retry_policies=RetryPoliciesConfig(
            policies=[
                RetryPolicyEntry(
                    workflow_name="wf",
                    breaker_window_min=15,
                    breaker_threshold=2,
                    require_idempotent=False,
                )
            ]
        ),
    )
    apply_retry_overrides(cfg)
    p = lookup("wf")
    assert p.breaker_window_min == 15
    assert p.breaker_threshold == 2
    assert p.require_idempotent is False
