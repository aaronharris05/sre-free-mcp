# security — Security Command Center findings forwarder

The security bot mirrors active findings from GCP's Security Command Center (SCC) into `gap_reports` so the rollup, RCA, and MCP query tools can treat them uniformly with audit findings.

**Module:** [`core/security/`](../../src/sre_free_mcp/core/security/)
**Task name:** `security_audit`
**Default schedule:** disabled by default — enable when `install.organization_id` is configured and SCC is set up

## How one audit works

1. List all `ACTIVE` SCC findings under the configured organization via the SecurityCenter API.
2. Map SCC severity (`CRITICAL` / `HIGH` / `MEDIUM` / `LOW`) → our severity enum.
3. Use SCC's `category` (lowercased) as our `gap_kind`.
4. Bulk-write findings to `gap_reports` with `scope='security'`.

## Rules produced

The bot doesn't apply rules per se — it forwards SCC's existing detections. `gap_kind` is whatever SCC put in `finding.category`. Common ones:

| SCC category → gap_kind | Typical severity |
|---|---|
| `public_bucket` | critical |
| `open_firewall` | high |
| `weak_ssl_policy` | medium |
| `default_network` | medium |
| `excessive_permissions` | high |
| `mfa_not_enforced` | high |

## Config

- **`install.organization_id`** must be set (or `GCP_ORGANIZATION_ID` env var). SCC findings live at the GCP organization level, not project level. When empty, the bot logs and no-ops cleanly.
- **SCC must be enabled** on the org. The free tier surfaces a subset; SCC Premium adds vulnerability scanners + compliance reports.
- **The SA needs `roles/securitycenter.findingsViewer`** on the org. The Terraform module does NOT grant this automatically — add it manually:

```bash
gcloud organizations add-iam-policy-binding YOUR_ORG_ID \
  --member="serviceAccount:sre-free-mcp@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/securitycenter.findingsViewer"
```

## Optional dependency

`google-cloud-securitycenter` ships in the `[security]` optional extra:

```bash
pip install sre-free-mcp[security]
```

The container image you build doesn't include it by default. If you want the security bot to actually fire, add it to your `pyproject.toml` deps before building.

## Tables read / written

**Reads** — Security Command Center API (org-level).

**Writes** — `governance.gap_reports` (scope='security').

## Example finding

```json
{
  "scope": "security",
  "scope_id": "//cloudresourcemanager.googleapis.com/projects/my-project/buckets/my-public-bucket",
  "gap_kind": "public_bucket",
  "severity": "critical",
  "details": {
    "scc_name": "organizations/123/sources/456/findings/abc",
    "category": "PUBLIC_BUCKET",
    "severity_raw": "CRITICAL",
    "resource_name": "//cloudresourcemanager.googleapis.com/projects/my-project/buckets/my-public-bucket",
    "event_time": "2026-05-12T14:30:00+00:00",
    "external_uri": "https://console.cloud.google.com/security/command-center/findings/...",
    "state": "ACTIVE"
  }
}
```

The `external_uri` deep-links back to SCC for full context.

## Why mirror SCC findings instead of just using SCC?

Three reasons:

1. **Unified surface** — `gap_reports` is queryable via the MCP server alongside anomaly / cost / freshness findings. One query, all signals.
2. **Workflow attribution** — when SCC findings tie to resources labeled with a `workflow_name`, the rollup + incidents bots can correlate security alerts with operational alerts.
3. **Retention control** — SCC findings can age out; mirroring keeps a historical record in your own BigQuery.

## Common questions

**Q: Why disabled by default?**
SCC requires org-level setup and an explicit IAM grant the Terraform module doesn't handle automatically. Default-off prevents new installs from seeing scary "permission denied" errors in their logs.

**Q: Do I need SCC Premium?**
For the bot to function, no — the free tier exposes findings. For useful coverage, mostly yes. Premium adds container vulnerability scanning, attack-path simulation, compliance-frameworks coverage. Without Premium you get a much smaller set of detections.

**Q: How do I filter out SCC findings I don't care about (false positives, accepted risks)?**
SCC has its own MUTE state. Mute findings in SCC; the bot only mirrors `state='ACTIVE'`, so muted ones are skipped.

**Q: Can I use this with Wiz / Prisma / other CSPM tools instead of SCC?**
Not in v1. Each CSPM has its own API shape. Easiest path: have the other tool publish findings into Pub/Sub or BQ, then write a small adapter that reads from there and writes to `gap_reports`. Or write a new subclass of the bot pattern.
