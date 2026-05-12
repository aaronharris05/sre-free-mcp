"""Thin GitHub REST client.

Just enough surface for the SCM audit: list branches, list recent
closed PRs, check approving-review count. Uses httpx; lazy-imports so
unit tests that stub the methods don't need a network stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


@dataclass(frozen=True)
class BranchInfo:
    name: str
    last_commit_at: datetime
    last_commit_sha: str


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    title: str
    state: str               # 'open' | 'closed'
    merged: bool
    merged_at: datetime | None
    created_at: datetime
    updated_at: datetime
    base_ref: str
    head_ref: str
    user: str
    approving_review_count: int


class GitHubClient:
    """Authenticated GitHub REST client.

    Pass a personal access token or GitHub App installation token.
    Tests construct a stub by subclassing and overriding the three
    public methods.
    """

    def __init__(self, *, token: str, base_url: str = _BASE) -> None:
        if not token:
            raise ValueError("GitHubClient requires a token")
        self.token = token
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        import httpx  # lazy

        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    # -----------------------------------------------------------------------
    # Read methods used by the audit
    # -----------------------------------------------------------------------

    def list_branches(self, owner: str, repo: str, *, limit: int = 100) -> list[BranchInfo]:
        """Return up to ``limit`` branches with their tip commit date."""
        data = self._get(
            f"/repos/{owner}/{repo}/branches",
            params={"per_page": min(limit, 100)},
        )
        out: list[BranchInfo] = []
        for entry in data[:limit]:
            commit_url = entry["commit"]["url"].replace(self.base_url, "")
            commit = self._get(commit_url)
            iso = (
                commit.get("commit", {})
                .get("committer", {})
                .get("date")
                or commit.get("commit", {}).get("author", {}).get("date")
            )
            if not iso:
                continue
            last_at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            out.append(
                BranchInfo(
                    name=entry["name"],
                    last_commit_at=last_at,
                    last_commit_sha=entry["commit"]["sha"],
                )
            )
        return out

    def list_recent_prs(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "all",
        per_page: int = 50,
    ) -> list[PullRequestInfo]:
        data = self._get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"},
        )
        out: list[PullRequestInfo] = []
        for entry in data:
            reviews = self._get(
                f"/repos/{owner}/{repo}/pulls/{entry['number']}/reviews"
            )
            approving = sum(1 for r in reviews if r.get("state") == "APPROVED")
            merged_at = entry.get("merged_at")
            out.append(
                PullRequestInfo(
                    number=entry["number"],
                    title=entry["title"],
                    state=entry["state"],
                    merged=bool(merged_at),
                    merged_at=(
                        datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                        if merged_at
                        else None
                    ),
                    created_at=datetime.fromisoformat(
                        entry["created_at"].replace("Z", "+00:00")
                    ),
                    updated_at=datetime.fromisoformat(
                        entry["updated_at"].replace("Z", "+00:00")
                    ),
                    base_ref=entry["base"]["ref"],
                    head_ref=entry["head"]["ref"],
                    user=entry["user"]["login"],
                    approving_review_count=approving,
                )
            )
        return out


__all__ = ["BranchInfo", "GitHubClient", "PullRequestInfo"]
