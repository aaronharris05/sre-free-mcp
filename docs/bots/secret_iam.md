# secret_iam — Secret Manager rotation + project IAM hygiene

The secret_iam bot audits two things about your GCP project: (1) Secret Manager rotation age, and (2) project-level IAM bindings for overprivileged or public bindings.

**Module:** [`core/secret_iam/`](../../src/sre_free_mcp/core/secret_iam/)
**Task name:** `secret_iam_audit`
**Default schedule:** `0 7 * * *` (daily 07:00 UTC)

## How one audit works

1. List every secret in the project via the Secret Manager API.
2. For each secret, find the most-recent enabled version and compare its `create_time` to the rotation threshold.
3. Read the project's IAM policy via the Resource Manager API.
4. Apply IAM rules to the bindings.
5. Bulk-write findings to `gap_reports` with `scope='secret_iam'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `no_active_secret_version` | high | Secret exists but no enabled versions — any reader fails |
| `unrotated_secret` | medium | Latest enabled version older than `rotation_threshold_days` (default 90) |
| `public_iam_binding` | critical | `allUsers` or `allAuthenticatedUsers` granted any role |
| `overprivileged_iam_role` | high | `roles/owner`, `roles/editor`, or `roles/iam.securityAdmin` granted to a `user:` or `group:` principal |

## Config

No YAML — the bot consumes Secret Manager + project IAM directly.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `rotation_threshold_days` | 90 | Secrets older than this fire `unrotated_secret` |

For tighter security postures, lower to 30 or 60. For dev projects with rarely-changing secrets, raise to 180.

## Tables read / written

**Reads** — Secret Manager API + Resource Manager API.

**Writes** — `governance.gap_reports` (scope='secret_iam').

## Example findings

```json
{
  "scope": "secret_iam",
  "scope_id": "secret:sre-smtp-password",
  "gap_kind": "unrotated_secret",
  "severity": "medium",
  "details": {
    "secret": "sre-smtp-password",
    "age_days": 142,
    "threshold_days": 90,
    "latest_version_created_at": "2025-12-22T08:14:00+00:00",
    "rule": "latest enabled version is > 90d old"
  }
}
```

```json
{
  "scope": "secret_iam",
  "scope_id": "iam:my-project:roles/owner:user:alice@example.com",
  "gap_kind": "overprivileged_iam_role",
  "severity": "high",
  "details": {
    "project_id": "my-project",
    "role": "roles/owner",
    "member": "user:alice@example.com",
    "rule": "roles/owner on user/group principal; prefer least-privilege custom role"
  }
}
```

## Why service accounts are exempt from overprivileged-role checks

A service account with `roles/owner` is its own concern (and a different smell), tracked elsewhere. The overprivileged rule specifically flags `user:` and `group:` members because human-typed-principal Owner role is what tends to leak between team handoffs. For service-account audit, see [security](security.md).

## Common questions

**Q: How do I exclude expected long-lived secrets?**
v1: the rule is global. Workaround: rotate the secret manually right after the audit, OR fork `evaluate_secret` to take an excluded-name list.

**Q: Does this catch IAM bindings on individual resources (a specific BQ dataset, a specific bucket)?**
Not in v1 — only project-level bindings. Per-resource IAM audit is a v2 candidate. Workaround: use SCC (forwarded by the [security](security.md) bot) which surfaces per-resource policy findings.

**Q: My SA has `roles/owner` because Terraform needs it. Why isn't that flagged?**
Because SAs are exempt from the overprivileged rule (see above). If you want them flagged too, edit `_OVERPRIVILEGED_ROLES` checks in `checks.py` to remove the `user:`/`group:` filter.

**Q: How do I rotate a secret without breaking active readers?**
Add a new enabled version, wait for clients to refresh (Cloud Run typically picks it up on the next cold-start), then disable the old version. The bot looks at `latest_enabled_version_created_at`, so adding a new version restarts the rotation clock immediately.
