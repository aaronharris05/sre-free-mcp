"""Smoke tests for the SCM sweep — stubs the GitHub client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.scm import audit
from sre_free_mcp.core.scm.client import BranchInfo, GitHubClient, PullRequestInfo
from sre_free_mcp.core.tables import GovernanceTables

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


class _StubGitHub(GitHubClient):
    def __init__(
        self,
        *,
        branches: list[BranchInfo] | None = None,
        prs: list[PullRequestInfo] | None = None,
        raise_on_listing: bool = False,
    ) -> None:
        super().__init__(token="stub")
        self._branches = branches or []
        self._prs = prs or []
        self._raise = raise_on_listing

    def list_branches(self, owner: str, repo: str, *, limit: int = 100):
        if self._raise:
            raise RuntimeError("simulated 403")
        return list(self._branches)

    def list_recent_prs(self, owner: str, repo: str, *, state: str = "all", per_page: int = 50):
        return list(self._prs)


def test_sweep_with_no_repos_returns_empty():
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        github_client=_StubGitHub(),
        repos=[],
        now=_NOW,
    )
    assert result.repos == 0
    assert result.findings == []


def test_sweep_writes_findings_with_scope_scm():
    branches = [
        BranchInfo(
            name="feature/old",
            last_commit_at=_NOW - timedelta(days=200),
            last_commit_sha="x",
        )
    ]
    bq = _FakeBQ()
    result = audit.sweep(
        project="p",
        bq_client=bq,
        github_client=_StubGitHub(branches=branches),
        repos=[("acme", "payments", "main")],
        now=_NOW,
    )
    assert any(f.scope == "scm" for f in result.findings)
    assert any(r["scope"] == "scm" for _, rows in bq.inserts for r in rows)


def test_sweep_isolates_per_repo_exceptions():
    """A failing repo doesn't stop the sweep — it just doesn't get scanned."""
    raising_client = _StubGitHub(raise_on_listing=True)
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        github_client=raising_client,
        repos=[("acme", "broken", "main")],
        now=_NOW,
    )
    assert result.repos == 0
    assert result.findings == []


def test_sweep_threads_tunable_stale_threshold():
    """A 30d-old branch fires under stale_branch_days=20 but not the default 90."""
    branches = [
        BranchInfo(
            name="feature/x",
            last_commit_at=_NOW - timedelta(days=30),
            last_commit_sha="x",
        )
    ]
    no_finding = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        github_client=_StubGitHub(branches=branches),
        repos=[("acme", "x", "main")],
        now=_NOW,
    )
    assert no_finding.findings == []

    fires = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        github_client=_StubGitHub(branches=branches),
        repos=[("acme", "x", "main")],
        now=_NOW,
        stale_branch_days=20,
    )
    assert any(f.gap_kind == "stale_branch" for f in fires.findings)


def test_sweep_uses_custom_dataset():
    branches = [
        BranchInfo(
            name="feature/old",
            last_commit_at=_NOW - timedelta(days=200),
            last_commit_sha="x",
        )
    ]
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        github_client=_StubGitHub(branches=branches),
        repos=[("acme", "x", "main")],
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(
            project="",
            bq_client=_FakeBQ(),
            github_client=_StubGitHub(),
            repos=[],
            now=_NOW,
        )


def test_github_client_requires_token():
    with pytest.raises(ValueError, match="token"):
        GitHubClient(token="")
