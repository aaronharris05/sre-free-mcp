"""Smoke tests for the dependency sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sre_free_mcp.core.dependency import audit
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def test_sweep_with_no_sources_returns_empty():
    result = audit.sweep(project="p", bq_client=_FakeBQ(), sources=[], now=_NOW)
    assert result.sources == 0
    assert result.dependencies == 0


def test_sweep_skips_missing_source(tmp_path: Path):
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        sources=[tmp_path / "does-not-exist.txt"],
        now=_NOW,
    )
    assert result.sources == 0


def test_sweep_emits_findings_for_unpinned_requirements(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("flask\ndjango>=5.0,<6\npydantic\n", encoding="utf-8")
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, sources=[req], now=_NOW)
    kinds = [f.gap_kind for f in result.findings]
    # flask + pydantic unpinned; django bounded → 2 unpinned findings
    assert kinds.count("unpinned_dependency") == 2
    inserts = [r for _, rows in bq.inserts for r in rows]
    assert all(r["scope"] == "dependency" for r in inserts)


def test_sweep_emits_findings_for_pyproject(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "x"
dependencies = ["flask", "django>=5,<6"]
""",
        encoding="utf-8",
    )
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), sources=[pyproject], now=_NOW
    )
    assert any(f.gap_kind == "unpinned_dependency" for f in result.findings)


def test_sweep_uses_custom_dataset(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("flask\n", encoding="utf-8")
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        sources=[req],
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), sources=[], now=_NOW)


def test_sweep_ignores_unrecognized_file_extensions(tmp_path: Path):
    f = tmp_path / "setup.cfg"
    f.write_text("[metadata]\nname=x\n", encoding="utf-8")
    result = audit.sweep(project="p", bq_client=_FakeBQ(), sources=[f], now=_NOW)
    assert result.sources == 0
