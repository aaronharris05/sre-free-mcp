"""SCM audit — sweep orchestrator."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import RepoSnapshot, evaluate
from .client import GitHubClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    repos: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    github_client: GitHubClient,
    repos: list[tuple[str, str, str]],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
    stale_branch_days: int = 90,
    long_open_pr_days: int = 30,
    require_approving_review: bool = True,
) -> AuditResult:
    """Run one SCM sweep over ``repos``.

    Args:
        repos: list of ``(owner, repo_name, default_branch)`` tuples.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    findings: list[Finding] = []
    scanned = 0

    for owner, name, default_branch in repos:
        try:
            branches = github_client.list_branches(owner, name)
            prs = github_client.list_recent_prs(owner, name, state="all")
            snapshot = RepoSnapshot(
                owner=owner,
                name=name,
                default_branch=default_branch,
                branches=branches,
                recent_prs=prs,
            )
            scanned += 1
        except Exception:
            logger.exception("scm read failed for %s/%s (non-fatal)", owner, name)
            continue
        findings.extend(
            evaluate(
                snapshot,
                now,
                stale_branch_days=stale_branch_days,
                long_open_pr_days=long_open_pr_days,
                require_approving_review=require_approving_review,
            )
        )

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.scm",
        )

    logger.info(
        "scm_complete repos=%d findings=%d",
        scanned,
        len(findings),
    )
    return AuditResult(
        repos=scanned,
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


__all__ = ["AuditResult", "sweep"]
