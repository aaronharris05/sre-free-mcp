"""Smoke tests for the secret_iam sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.secret_iam import audit
from sre_free_mcp.core.secret_iam.checks import IamBinding, SecretSnapshot
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch(monkeypatch, *, secrets: list[SecretSnapshot], bindings: list[IamBinding]):
    monkeypatch.setattr(audit, "list_secrets", lambda p: list(secrets))
    monkeypatch.setattr(
        audit, "read_project_iam_bindings", lambda p: list(bindings)
    )


def test_sweep_with_no_inputs_returns_empty(monkeypatch):
    _patch(monkeypatch, secrets=[], bindings=[])
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    assert result.secrets_scanned == 0
    assert result.iam_bindings_scanned == 0
    assert result.findings == []


def test_sweep_flags_unrotated_secret(monkeypatch):
    _patch(
        monkeypatch,
        secrets=[
            SecretSnapshot(
                name="smtp",
                latest_enabled_version_created_at=_NOW - timedelta(days=200),
                enabled_version_count=1,
            )
        ],
        bindings=[],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(f.gap_kind == "unrotated_secret" for f in result.findings)
    assert any(r["scope"] == "secret_iam" for _, rows in bq.inserts for r in rows)


def test_sweep_flags_public_iam_binding(monkeypatch):
    _patch(
        monkeypatch,
        secrets=[],
        bindings=[
            IamBinding(role="roles/storage.objectViewer", members=("allUsers",))
        ],
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, now=_NOW)
    assert any(
        f.gap_kind == "public_iam_binding" and f.severity == "critical"
        for f in result.findings
    )


def test_sweep_threads_rotation_threshold(monkeypatch):
    """A 45d-old secret fires under tighter threshold but not default."""
    _patch(
        monkeypatch,
        secrets=[
            SecretSnapshot(
                name="smtp",
                latest_enabled_version_created_at=_NOW - timedelta(days=45),
                enabled_version_count=1,
            )
        ],
        bindings=[],
    )
    bq = _FakeBQ()
    assert audit.sweep(project="p", bq_client=bq, now=_NOW).findings == []
    fires = audit.sweep(
        project="p", bq_client=_FakeBQ(), now=_NOW, rotation_threshold_days=30
    )
    assert any(f.gap_kind == "unrotated_secret" for f in fires.findings)


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch(
        monkeypatch,
        secrets=[
            SecretSnapshot(
                name="smtp",
                latest_enabled_version_created_at=_NOW - timedelta(days=200),
                enabled_version_count=1,
            )
        ],
        bindings=[],
    )
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project(monkeypatch):
    _patch(monkeypatch, secrets=[], bindings=[])
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), now=_NOW)
