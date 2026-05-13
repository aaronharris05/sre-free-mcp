# freshness — detect tables that aren't being updated on their declared cadence

The freshness bot flags BigQuery tables that have gone stale — either past their expected refresh window or sitting empty after a write.

**Module:** [`core/freshness/`](../../src/sre_free_mcp/core/freshness/)
**Task name:** `freshness_sweep`
**Default schedule:** `30 4 * * *` (daily 04:30 UTC)

## How one sweep works

For each active target in `freshness_targets.yaml`:

1. Read `last_modified_time` + `total_rows` from `INFORMATION_SCHEMA.TABLE_STORAGE`.
2. Compare age against `expected_cadence_hours` (multiplied by yellow/red thresholds).
3. Compare row count against an emptiness threshold.
4. Bulk-write findings to `gap_reports` with `scope='freshness'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `table_stale` | critical | `age > stale_red_multiplier × cadence` (default 5×) |
| `table_stale` | high | `age > stale_yellow_multiplier × cadence` (default 2×) |
| `table_empty` | medium | `row_count = 0 AND age > empty_min_hours` (default 24h) |

## Config

[`freshness_targets.yaml`](../configuration.md#freshness_targetsyaml). Each entry declares one table with its expected refresh cadence:

```yaml
- dataset: raw
  table: api_latency_hourly
  expected_cadence_hours: 1.5      # 1h + 30min slack
  owner_team: data_owners
  purpose: API latency metrics hourly ingest
```

Tables not listed here are NOT audited — explicit opt-in, no implicit "every table in dataset X has SLA Y" rules.

Cross-validated at startup: no duplicate `(dataset, table)` pairs; `owner_team` exists in `recipients.yaml`.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `stale_yellow_multiplier` | 2.0 | First-threshold severity (high) |
| `stale_red_multiplier` | 5.0 | Second-threshold severity (critical) |
| `empty_min_hours` | 24.0 | How long an empty table must sit before `table_empty` fires |
| `write` | `True` | Set False for dry-runs |

Tuning is global to the sweep; per-target multipliers are a v2 candidate if needed.

## SQL-injection safety

`INFORMATION_SCHEMA.TABLE_STORAGE` paths can't be parameterized, so the audit must interpolate dataset + table identifiers into SQL. Before interpolation, every identifier is validated against `^[A-Za-z_][A-Za-z0-9_]*$`. Anything outside that pattern raises `ValueError` and is skipped — no SQL injection possible.

## Tables read / written

**Reads**

- `<configured_dataset>.INFORMATION_SCHEMA.TABLE_STORAGE` — last modified + row count

**Writes**

- `governance.gap_reports` (scope='freshness')

## Example finding

```json
{
  "scope": "freshness",
  "scope_id": "raw.api_latency_hourly",
  "gap_kind": "table_stale",
  "severity": "high",
  "details": {
    "dataset": "raw",
    "table": "api_latency_hourly",
    "owner_team": "data_owners",
    "purpose": "API latency metrics hourly ingest",
    "rule": "age > 2.0x expected cadence",
    "last_modified": "2026-05-12T03:15:42+00:00",
    "age_hours": 4.3,
    "expected_cadence_hours": 1.5
  }
}
```

## Why row_count=0 doesn't fire immediately

A new table or one being recreated may legitimately have `row_count=0` for a few minutes. The `empty_min_hours` window (default 24h) prevents transient false positives — a table only fires `table_empty` if it's been empty for a full day after its last_modified bump.

## Common questions

**Q: Can I audit a table in a different project than the install?**
Yes — drop the project from the path (the bot prepends the install's `project_id` automatically) and grant the install's SA `roles/bigquery.metadataViewer` on the foreign dataset.

**Q: What if `INFORMATION_SCHEMA.TABLE_STORAGE` returns no rows for a target?**
The bot logs a "table not found" warning and skips. No finding is produced — the target stays in the config but is effectively dormant until the table exists.

**Q: How is this different from `job_uptime`?**
job_uptime audits **processes** (Cloud Run jobs that should be running). freshness audits **data products** (tables that should be receiving rows). A workflow can be running on schedule but writing stale data (e.g., upstream API returning the same payload); freshness catches that.

**Q: Can I use this for files in GCS instead of BQ tables?**
Not directly — the rules query INFORMATION_SCHEMA. Closest workaround: have a scheduled query mirror GCS object metadata into a BigQuery table, then audit that table.
