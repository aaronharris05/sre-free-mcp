# data_catalog — structural completeness of the workflow registry

The data_catalog bot audits every row in `governance.workflows` for required-field completeness. It's the "structural" half of the catalog audit pair — [ai_gov](ai_gov.md) handles the "policy compliance" half.

**Module:** [`core/data_catalog/`](../../src/sre_free_mcp/core/data_catalog/)
**Task name:** `data_catalog_audit`
**Default schedule:** `0 7 * * 1` (weekly Monday 07:00 UTC)

## How one audit works

1. Read every `status='active'` row from `governance.workflows`.
2. Apply five completeness rules.
3. Bulk-write findings to `gap_reports` with `scope='catalog'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `missing_source_path` | medium | `source_path IS NULL` — breaks SCM + RCA linkage |
| `missing_owner_team` | high | `owner_team IS NULL` — finding emails fall through to fallback |
| `missing_idempotent_flag` | medium | `idempotent IS NULL` — retry orchestrator vetoes every retry |
| `missing_cron_for_scheduler` | high | `trigger_kind='scheduler' AND cron IS NULL` — job_uptime missed-run rule can't evaluate |
| `unparseable_cron` | low | `cron` set but doesn't match a recognized pattern — missed-run rule fails open |

## Config

No YAML — the bot consumes the registry directly. To eliminate findings: ensure every active workflow has all required fields populated when registered (via the `register_workflow` MCP tool or direct SQL INSERT).

## Tables read / written

**Reads** — `governance.workflows`.

**Writes** — `governance.gap_reports` (scope='catalog').

## Example finding

```json
{
  "scope": "catalog",
  "scope_id": "ingest_users",
  "gap_kind": "missing_idempotent_flag",
  "severity": "medium",
  "details": {
    "workflow": "ingest_users",
    "rule": "idempotent is NULL; retry orchestrator vetoes every retry until explicitly set TRUE or FALSE"
  }
}
```

## Why NULL idempotent is medium, not high

`NULL` is a deliberate "I haven't decided yet" signal — the retry orchestrator respects it by refusing to retry. That's safer than treating `NULL` as `FALSE` and silently never retrying. The finding is medium because it's an incomplete registration, not an actively-wrong one. Setting `idempotent=FALSE` explicitly is a valid state and produces no finding.

## Interaction with ai_gov

data_catalog and ai_gov both audit the workflows table but check different fields. data_catalog covers the SRE-mechanics fields (cron, idempotent, source_path). ai_gov covers the documentation/compliance fields (business_purpose, policy_refs). They typically run on the same cron and produce non-overlapping findings.

## Common questions

**Q: Can I customize which fields are required?**
Not via YAML today. The rules are in `checks.py` — fork and tune if your org has different requirements (e.g., source_path required as high severity instead of medium).

**Q: What if I want to deregister a workflow without deleting the row?**
Set `status='archived'` (or anything other than `'active'`). The audit only scans active rows.

**Q: Why does the unparseable_cron rule fire as low?**
Because the missed-run rule already fails open on unknown cron patterns — the workflow just isn't subjected to that check. The low-severity finding is informational ("you have an exotic cron we can't reason about") rather than urgent.
