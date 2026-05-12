"""Pure-logic SCM rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sre_free_mcp.core.models import Finding

from .client import BranchInfo, PullRequestInfo


@dataclass
class RepoSnapshot:
    """One repo's branch + PR evidence."""

    owner: str
    name: str
    default_branch: str
    branches: list[BranchInfo] = field(default_factory=list)
    recent_prs: list[PullRequestInfo] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def evaluate(
    snapshot: RepoSnapshot,
    now: datetime,
    *,
    stale_branch_days: int = 90,
    long_open_pr_days: int = 30,
    require_approving_review: bool = True,
) -> list[Finding]:
    """Apply SCM rules to one repo snapshot."""
    findings: list[Finding] = []
    base = {"repo": snapshot.slug, "default_branch": snapshot.default_branch}

    # Stale branches — exclude the default branch (always "alive").
    stale_cutoff = now - timedelta(days=stale_branch_days)
    for b in snapshot.branches:
        if b.name == snapshot.default_branch:
            continue
        if b.last_commit_at < stale_cutoff:
            findings.append(
                Finding(
                    scope="scm",
                    scope_id=f"{snapshot.slug}@{b.name}",
                    gap_kind="stale_branch",
                    severity="medium",
                    details={
                        **base,
                        "branch": b.name,
                        "last_commit_at": b.last_commit_at.isoformat(),
                        "age_days": (now - b.last_commit_at).days,
                        "threshold_days": stale_branch_days,
                    },
                )
            )

    # Long-open PRs.
    open_cutoff = now - timedelta(days=long_open_pr_days)
    for pr in snapshot.recent_prs:
        if pr.state != "open":
            continue
        if pr.created_at < open_cutoff:
            findings.append(
                Finding(
                    scope="scm",
                    scope_id=f"{snapshot.slug}#{pr.number}",
                    gap_kind="long_open_pr",
                    severity="medium",
                    details={
                        **base,
                        "pr_number": pr.number,
                        "title": pr.title,
                        "user": pr.user,
                        "open_days": (now - pr.created_at).days,
                        "threshold_days": long_open_pr_days,
                    },
                )
            )

    # Unreviewed merges into default branch.
    if require_approving_review:
        for pr in snapshot.recent_prs:
            if not pr.merged:
                continue
            if pr.base_ref != snapshot.default_branch:
                continue
            if pr.approving_review_count == 0:
                findings.append(
                    Finding(
                        scope="scm",
                        scope_id=f"{snapshot.slug}#{pr.number}",
                        gap_kind="unreviewed_merge",
                        severity="high",
                        details={
                            **base,
                            "pr_number": pr.number,
                            "title": pr.title,
                            "user": pr.user,
                            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                            "approving_review_count": pr.approving_review_count,
                        },
                    )
                )

    return findings


__all__ = ["RepoSnapshot", "evaluate"]
