"""Unit tests for the PII target schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_free_mcp.core.pii.targets import PiiTarget, PiiTargetsConfig


def test_target_requires_non_empty_dataset_and_table():
    with pytest.raises(ValidationError):
        PiiTarget(dataset="", table="x")
    with pytest.raises(ValidationError):
        PiiTarget(dataset="x", table="   ")


def test_default_info_types_include_common_pii():
    t = PiiTarget(dataset="customers", table="users")
    assert "EMAIL_ADDRESS" in t.info_types
    assert "PHONE_NUMBER" in t.info_types
    assert "US_SOCIAL_SECURITY_NUMBER" in t.info_types


def test_target_is_frozen():
    t = PiiTarget(dataset="d", table="t")
    with pytest.raises(ValidationError):
        t.sample_rows = 9999  # type: ignore[misc]


def test_targets_config_rejects_duplicates():
    with pytest.raises(ValidationError, match="duplicate"):
        PiiTargetsConfig(
            targets=[
                PiiTarget(dataset="d", table="x"),
                PiiTarget(dataset="d", table="x"),
            ]
        )


def test_fqn_property():
    assert PiiTarget(dataset="customers", table="users").fqn == "customers.users"


def test_zero_sample_rows_rejected():
    with pytest.raises(ValidationError):
        PiiTarget(dataset="d", table="t", sample_rows=0)
