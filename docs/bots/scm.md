# scm — git / GitHub hygiene audit

The scm bot queries the GitHub REST API for configured repos and flags hygiene issues: stale branches, unreviewed merges, long-open PRs.

**Module:** [`core/scm/`](../../src/sre_free_mcp/core/scm/)
**Task name:** `scm_audit`
**Default schedule:** disabled by default — enable when you've populated env vars

## How one audit works

For each repo in `SRE_SCM_REPOS`:

1. List branches via `GET /repos/{owner}/{repo}/branches` (last commit date).
2. List recent PRs via `GET /repos/{owner}/{repo}/pulls?state=all` (with approving-review count).
3. Apply three hygiene rules per repo.
4. Bulk-write findings to `gap_reports` with `scope='scm'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `stale_branch` | medium | Branch with no commits in `stale_branch_days` (default 90). Default branch always exempt. |
| `long_open_pr` | medium | PR open for `> long_open_pr_days` (default 30) without merge or close |
| `unreviewed_merge` | high | PR merged into default branch with 0 approving reviews (gated by `require_approving_review`) |

## Config

Via env vars on the Cloud Run job:

```bash
SRE_SCM_REPOS=owner1/repo1/main,owner2/repo2/develop
SRE_SCM_TOKEN_SECRET=github-pat
```

| Var | Purpose |
|---|---|
| `SRE_SCM_REPOS` | Comma-separated `owner/repo/default_branch` triples. |
| `SRE_SCM_TOKEN_SECRET` | Name of a Secret Manager secret holding a GitHub PAT (or installation token). |

The PAT needs `repo` scope (or `public_repo` for public repos only).

## Tunable knobs

`sweep()` (via the runner task) exposes:

| Arg | Default | Notes |
|---|---|---|
| `stale_branch_days` | 90 | Threshold for branch staleness |
| `long_open_pr_days` | 30 | Threshold for unmerged-PR age |
| `require_approving_review` | True | Set False if your team merges without review by convention |

## Tables read / written

**Reads** — GitHub REST API (read-only — the bot NEVER modifies repos).

**Writes** — `governance.gap_reports` (scope='scm').

## Example finding

```json
{
  "scope": "scm",
  "scope_id": "acme/payments#1234",
  "gap_kind": "unreviewed_merge",
  "severity": "high",
  "details": {
    "repo": "acme/payments",
    "default_branch": "main",
    "pr_number": 1234,
    "title": "Bump auth library to 4.0",
    "user": "alice",
    "merged_at": "2026-05-12T20:14:30+00:00",
    "approving_review_count": 0
  }
}
```

## Per-repo isolation

One bad repo (deleted, permission-denied, rate-limited) doesn't block the others. Errors are caught per-repo and logged; the sweep continues.

## GitHub rate limits

The PAT's hourly rate limit is 5,000 requests. Each repo costs ~2 + 1-per-PR requests (branches + reviews per PR). 100 PRs in 10 repos ≈ 1,020 requests. Well within budget; the audit can run hourly if you want.

For higher volume use a GitHub App installation token (15,000/hour per installation).

## Common questions

**Q: Why doesn't this support GitLab / Bitbucket?**
The v1 client wraps GitHub's REST API. Adding a GitLab client is straightforward — define `GitLabClient` with the same `list_branches` + `list_recent_prs` interface in `core/scm/client.py`, instantiate based on a config switch. Same rule logic, no `checks.py` changes needed.

**Q: How do I exclude bot-merged PRs (Renovate / Dependabot)?**
Filter on `pr.user` before passing to `evaluate()`. Or write a more nuanced rule: "unreviewed_merge fires unless `pr.user` is in an `allowed_automerge_actors` list" — modify `checks.py`.

**Q: Stale branches are firing on long-running feature branches we want to keep.**
Two options: (a) raise `stale_branch_days` globally; (b) extend `evaluate()` to accept an `excluded_branches` list. Branches matching a regex would skip the rule.

**Q: Can I get a finding when a force-push happens on main?**
Not in v1 — the bot doesn't track ref history. Force-push detection is a different signal (use GitHub webhooks or audit logs) and a v2 candidate.

**Q: Does signed-commit verification work?**
Not in v1. The GitHub API returns commit-signature info; the rule logic to enforce `commit.verification.verified == True` is a v2 candidate. Until then, configure GitHub branch protection to require signed commits.
