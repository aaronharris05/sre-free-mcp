"""Unit tests for SCM rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sre_free_mcp.core.scm.checks import RepoSnapshot, evaluate
from sre_free_mcp.core.scm.client import BranchInfo, PullRequestInfo

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _snapshot(
    branches: list[BranchInfo] | None = None,
    prs: list[PullRequestInfo] | None = None,
) -> RepoSnapshot:
    return RepoSnapshot(
        owner="acme",
        name="payments",
        default_branch="main",
        branches=branches or [],
        recent_prs=prs or [],
    )


def _branch(name: str, *, last_commit_at: datetime) -> BranchInfo:
    return BranchInfo(name=name, last_commit_at=last_commit_at, last_commit_sha="deadbeef")


def _pr(
    *,
    number: int = 1,
    state: str = "open",
    merged: bool = False,
    merged_at: datetime | None = None,
    created_at: datetime | None = None,
    base_ref: str = "main",
    reviews: int = 1,
) -> PullRequestInfo:
    return PullRequestInfo(
        number=number,
        title=f"PR {number}",
        state=state,
        merged=merged,
        merged_at=merged_at,
        created_at=created_at or _NOW,
        updated_at=_NOW,
        base_ref=base_ref,
        head_ref="feature/x",
        user="alice",
        approving_review_count=reviews,
    )


# ---------------------------------------------------------------------------
# stale_branch
# ---------------------------------------------------------------------------


def test_stale_branch_fires_past_threshold():
    s = _snapshot(branches=[_branch("feature/old", last_commit_at=_NOW - timedelta(days=200))])
    findings = evaluate(s, _NOW)
    assert any(f.gap_kind == "stale_branch" for f in findings)


def test_default_branch_never_flagged_as_stale():
    """Even an inactive default branch shouldn't be flagged stale."""
    s = _snapshot(branches=[_branch("main", last_commit_at=_NOW - timedelta(days=365))])
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "stale_branch" not in kinds


def test_stale_threshold_is_tunable():
    """Recently-quiet branch fires under tight threshold but not default."""
    s = _snapshot(branches=[_branch("feature/x", last_commit_at=_NOW - timedelta(days=10))])
    assert not any(f.gap_kind == "stale_branch" for f in evaluate(s, _NOW))
    assert any(
        f.gap_kind == "stale_branch"
        for f in evaluate(s, _NOW, stale_branch_days=5)
    )


# ---------------------------------------------------------------------------
# long_open_pr
# ---------------------------------------------------------------------------


def test_long_open_pr_fires_past_threshold():
    s = _snapshot(prs=[_pr(created_at=_NOW - timedelta(days=45), state="open")])
    assert any(f.gap_kind == "long_open_pr" for f in evaluate(s, _NOW))


def test_closed_pr_not_flagged_long_open():
    s = _snapshot(prs=[_pr(created_at=_NOW - timedelta(days=45), state="closed")])
    kinds = {f.gap_kind for f in evaluate(s, _NOW)}
    assert "long_open_pr" not in kinds


# ---------------------------------------------------------------------------
# unreviewed_merge
# ---------------------------------------------------------------------------


def test_unreviewed_merge_into_default_branch_fires_high():
    pr = _pr(
        state="closed",
        merged=True,
        merged_at=_NOW,
        base_ref="main",
        reviews=0,
    )
    findings = evaluate(_snapshot(prs=[pr]), _NOW)
    matched = [f for f in findings if f.gap_kind == "unreviewed_merge"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_reviewed_merge_does_not_fire():
    pr = _pr(state="closed", merged=True, merged_at=_NOW, reviews=2)
    kinds = {f.gap_kind for f in evaluate(_snapshot(prs=[pr]), _NOW)}
    assert "unreviewed_merge" not in kinds


def test_merge_into_non_default_branch_does_not_fire():
    pr = _pr(
        state="closed",
        merged=True,
        merged_at=_NOW,
        base_ref="develop",
        reviews=0,
    )
    kinds = {f.gap_kind for f in evaluate(_snapshot(prs=[pr]), _NOW)}
    assert "unreviewed_merge" not in kinds


def test_review_requirement_can_be_disabled():
    pr = _pr(state="closed", merged=True, merged_at=_NOW, reviews=0)
    kinds = {
        f.gap_kind
        for f in evaluate(
            _snapshot(prs=[pr]), _NOW, require_approving_review=False
        )
    }
    assert "unreviewed_merge" not in kinds
