# anomaly — Isolation Forest outliers on BigQuery columns

The anomaly bot runs a daily sweep over configured `(table, metric_column)` targets, fits an Isolation Forest on `lookback_days` of history, and flags rows past per-target z-equivalent thresholds. Findings are routed to the configured `owner_team`.

**Module:** [`core/anomaly/`](../../src/sre_free_mcp/core/anomaly/)
**Task name:** `anomaly_sweep`
**Default schedule:** `0 4 * * *` (daily 04:00 UTC)

## How one sweep works

For each active target in `anomaly_targets.yaml`:

1. Read `lookback_days` of `(timestamp_column, metric_column)` from BigQuery.
2. If fewer than 30 non-null values, log "thin window" and skip.
3. Fit `IsolationForest(contamination=0.05, n_estimators=100)` on the standardized metric column.
4. Flip sklearn's score sign so "higher = more anomalous" matches z-equivalent convention.
5. Normalize raw scores to z-equivalents (mean 0, unit variance against the score distribution).
6. For each row, if `|z| >= severity_red_z` → severity `high`; if `|z| >= severity_yellow_z` → severity `medium`.
7. Bulk-insert findings into `gap_reports` with `scope='data'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `anomaly_zscore` | high | `|z| >= target.severity_red_z` |
| `anomaly_zscore` | medium | `|z| >= target.severity_yellow_z` (and not red) |

The same `gap_kind` covers both severities — they differ only in their `details.z_score`.

## Config

[`anomaly_targets.yaml`](../configuration.md#anomaly_targetsyaml). Each entry:

```yaml
- table: example_dataset.api_latency_hourly
  metric_column: latency_p95_ms
  timestamp_column: ts
  lookback_days: 14
  severity_red_z: 5.0
  severity_yellow_z: 3.5
  owner_team: data_owners
  purpose: API latency p95 outlier (data quality)
```

Cross-validated at startup: `severity_red_z >= severity_yellow_z`, `owner_team` exists in `recipients.yaml`, no duplicate `(table, metric_column)` pairs.

## Engine choice — IsolationForest only for v1

The bundled engine (`core/anomaly/engine.py`) uses scikit-learn's IsolationForest exclusively. It's the most reliable single-model choice for tabular outlier detection, runs fast (~1s per 1000 rows), and doesn't need labeled training data.

A leaderboard of detectors (IF + LOF + OCSVM + ECOD + PCA reconstruction) is on the v2 roadmap. Until then, customers wanting a different algorithm can subclass `engine.score()` or run their own scoring pre-pass and write findings directly.

## Routing — owner_team

Findings carry `details.owner_team` set from the target's `owner_team`. The [rollup](rollup.md) bot groups findings by team and the (future) anomaly-email task ships one email per team.

When a finding can't be matched to a configured target — e.g., a row from a table not in `anomaly_targets.yaml` somehow appearing in `gap_reports` — the router falls back to `governance_owners`.

## Tables read / written

**Reads** — the configured target tables (any project the SA has access to, but typically the install's own project).

**Writes** — `governance.gap_reports`.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `targets` | from config | List of `AnomalyTarget`. |
| `write_findings` | `True` | Set False for dry-runs (no BQ insert). |

Per-target tuning lives in the YAML (`severity_red_z`, `severity_yellow_z`, `lookback_days`).

## Example finding

```json
{
  "scope": "data",
  "scope_id": "example_dataset.api_latency_hourly:latency_p95_ms",
  "gap_kind": "anomaly_zscore",
  "severity": "high",
  "details": {
    "table": "example_dataset.api_latency_hourly",
    "metric_column": "latency_p95_ms",
    "z_score": 6.4,
    "value": 1840.2,
    "as_of": "2026-05-12T03:00:00+00:00",
    "owner_team": "data_owners",
    "model": "isolation_forest",
    "purpose": "API latency p95 outlier (data quality)"
  }
}
```

## Common questions

**Q: My target has only 15 rows but I want to scan it anyway.**
Lower the `_MIN_FIT_WINDOW = 30` constant in `engine.py`. Below ~30 points the detector's output isn't statistically meaningful, but for sanity-check use cases you can drop it.

**Q: Can I scan a column other than the configured `metric_column`?**
No — one target = one metric. Configure multiple targets if you want to scan multiple columns on the same table.

**Q: How do I reconcile this with my existing monitoring tools (Datadog, Prometheus)?**
You don't — anomaly is for *data outliers* (rows in your BigQuery tables), not service metrics. Use the [cloud_monitoring](cloud_monitoring.md) bot for GCP service alerts; pipe Datadog/Prometheus alerts into the cloud_monitoring sweep via a custom integration if you want unified `gap_reports`.

**Q: How do I tune severity thresholds?**
Start with the defaults (`red_z=5.0`, `yellow_z=3.5`). If you see too many false positives, raise both. If you see drift that obvious-by-eye isn't being caught, lower yellow first. The `as_of` and `value` in `details` help spot-check whether a flagged point is truly anomalous.

**Q: Does this email me directly?**
Not yet — the anomaly bot writes findings; the [rollup](rollup.md) bot emails them on a weekly cadence. A dedicated anomaly email (per-day per-team) is on the roadmap; for now, query `gap_reports` directly or wait for the rollup.
