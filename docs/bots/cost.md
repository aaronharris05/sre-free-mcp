# cost — daily-spend z-score against a rolling 28-day baseline

The cost bot flags daily spend rows whose z-score against a pre-computed 28-day baseline crosses configurable thresholds.

**Module:** [`core/cost/`](../../src/sre_free_mcp/core/cost/)
**Task name:** `cost_sweep`
**Default schedule:** `0 5 * * *` (daily 05:00 UTC)

## How one sweep works

1. Query the most-recent partition of `governance.cost_daily` (which the customer's own ETL fills from BigQuery billing export).
2. For each row, apply three guards:
   - `cost_usd >= min_spend_usd` (default $1)
   - `baseline_avg_28d >= min_baseline_usd` (default $0.50)
   - `|z_score| >= z_yellow` (default 3.0)
3. Flag `severity='critical'` if `|z_score| >= z_red` (default 5.0), else `'high'`.
4. Bulk-write findings to `gap_reports` with `scope='cost'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `cost_anomaly` | critical | `|z| >= z_red` AND guards pass |
| `cost_anomaly` | high | `|z| >= z_yellow` AND guards pass |

Negative z-scores (anomalously LOW spend) are surfaced too — sometimes "billing dropped to zero" is the real signal.

## Where `cost_daily` comes from

This bot reads pre-aggregated rows from `governance.cost_daily`. **sre-free-mcp does NOT populate this table** — it expects you to set up your own ETL from the BigQuery billing export.

Recommended approach: a BigQuery scheduled query (managed in Terraform) that runs at 04:30 UTC each day:

```sql
INSERT INTO `PROJECT.governance.cost_daily`
WITH today AS (
  SELECT
    DATE(usage_start_time) AS usage_date,
    service.description AS service,
    sku.description AS sku,
    labels.value AS workflow_name,
    SUM(cost) AS cost_usd,
    SUM(usage.amount) AS usage_amount,
    ANY_VALUE(usage.unit) AS usage_unit
  FROM `PROJECT.billing_export.gcp_billing_export_v1_XXXXX`,
       UNNEST(labels) AS labels
  WHERE DATE(usage_start_time) = CURRENT_DATE() - 1
    AND labels.key = 'workflow_name'
  GROUP BY usage_date, service, sku, workflow_name
),
baseline AS (
  SELECT
    service, workflow_name,
    AVG(cost_usd) AS baseline_avg_28d,
    STDDEV(cost_usd) AS baseline_std_28d
  FROM `PROJECT.governance.cost_daily`
  WHERE usage_date BETWEEN CURRENT_DATE() - 29 AND CURRENT_DATE() - 1
  GROUP BY service, workflow_name
)
SELECT
  t.usage_date, t.service, t.sku, t.workflow_name,
  t.cost_usd, t.usage_amount, t.usage_unit,
  b.baseline_avg_28d,
  SAFE_DIVIDE(t.cost_usd - b.baseline_avg_28d, b.baseline_std_28d) AS z_score,
  'billing_export_scheduled_query' AS source,
  CURRENT_TIMESTAMP() AS ingest_at
FROM today t
LEFT JOIN baseline b USING (service, workflow_name)
```

A Terraform-managed version of this query is a v2 candidate. Until then, customers set up the scheduled query manually.

## The three guards — why they exist

- **`min_spend_usd`** ($1 default) — A $0.04 → $0.20 jump is technically 5× growth, but operationally noise. Surfacing it just clutters the queue.
- **`min_baseline_usd`** ($0.50 default) — A brand-new service ramping from $0.05/day baseline to $5 on day 1 is normal first-day usage, not an anomaly.
- **z-score thresholds** — 3σ (yellow) is the standard "interesting" line; 5σ (critical) is the "this needs attention now" line.

All three are tunable on the `evaluate()` call.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `z_yellow` | 3.0 | High-severity threshold |
| `z_red` | 5.0 | Critical-severity threshold |
| `min_spend_usd` | 1.0 | Floor — rows below this never fire |
| `min_baseline_usd` | 0.50 | Floor — services with smaller baselines never fire |
| `write` | `True` | Set False for dry-runs |

## Tables read / written

**Reads** — `governance.cost_daily` (populated by your own ETL).

**Writes** — `governance.gap_reports` (scope='cost').

## Example finding

```json
{
  "scope": "cost",
  "scope_id": "BigQuery:my_workflow",
  "gap_kind": "cost_anomaly",
  "severity": "high",
  "details": {
    "as_of": "2026-05-12",
    "service": "BigQuery",
    "workflow_name": "my_workflow",
    "cost_usd": 240.50,
    "baseline_avg_28d": 18.20,
    "z_score": 4.1,
    "spike_ratio": 13.2,
    "rule": "|z| >= 3.0 (yellow) / 5.0 (red); cost >= $1"
  }
}
```

## Common questions

**Q: I see `cost_anomaly` findings but my BQ spend looks fine. What happened?**
Check `details.service`. Cost anomalies are per-service-per-workflow — a single bad day on Cloud Storage or a misconfigured Cloud Run job's egress can fire without showing up in the overall total.

**Q: Why daily granularity instead of hourly?**
GCP's billing export is hourly but the z-score noise floor at hourly is too high to be useful. Daily aggregation smooths transient spikes that don't matter. If you want hourly cost anomalies, fork the ETL and the rule.

**Q: How do I exclude expected spikes (a planned backfill, etc.)?**
Mark the day in your ETL — e.g., add a `planned_spike` column to `cost_daily` and filter it out before computing the baseline. Then the next day's normal spend doesn't look "anomalously low" relative to the spike.

**Q: Negative z-scores fire too. Is that intentional?**
Yes. "Spend collapsed to zero" is often the most-actionable cost signal (the workflow stopped writing, the bucket got deleted, etc.). If you really don't want low-side flags, edit `abs_z = abs(snapshot.z_score)` in `checks.py` to use raw z and gate on `z >= z_yellow`.
