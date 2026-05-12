"""SCM audit — git / GitHub hygiene.

Queries the GitHub REST API for one or more configured repositories
and flags hygiene issues. Read-only; never pushes or mutates.

Rules:
  - ``stale_branch``        — branch with no commits in N days (medium)
  - ``unreviewed_merge``    — PR merged into default branch with zero
                              approving reviews (high)
  - ``long_open_pr``        — PR open for > N days without merge or
                              close (medium)
"""

from .audit import AuditResult, sweep
from .checks import RepoSnapshot, evaluate
from .client import GitHubClient

__all__ = ["AuditResult", "GitHubClient", "RepoSnapshot", "evaluate", "sweep"]
