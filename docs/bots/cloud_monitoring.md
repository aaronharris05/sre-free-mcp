# cloud_monitoring — alert policy sync + active-incident pull

The cloud_monitoring bot is two routines packaged together: a **declarative alert-policy reconciler** (YAML → GCP Monitoring API) and an **active-incident sweeper** that mirrors currently-firing alerts into `gap_reports`.

**Module:** [`core/cloud_monitoring/`](../../src/sre_free_mcp/core/cloud_monitoring/)
**Task names:** `cloud_monitoring_sweep` (active pull) and `cloud_monitoring_sync` (declarative reconcile)
**Default schedule:** `*/15 * * * *` (sweep); `cloud_monitoring_sync` is disabled by default — run manually after editing `alert_policies.yaml`

## Two routines, one module

### `sync_policies(project, policies)` — declarative reconcile

Reads every entry in `alert_policies.yaml` and reconciles against the existing GCP Monitoring alert policies:

- Policy with matching `display_name` not present → CREATE
- Policy present but `enabled` or `documentation` differs → UPDATE
- Policy present and unchanged → no-op
- Policy present in GCP but not in YAML → leaves it alone (no automatic delete in v1)

Returns a summary dict: `{created, updated, unchanged, deleted}`.

Per-policy errors are caught + logged so one bad policy doesn't block the rest of the sync.

### `sweep(project, bq_client, organization_id)` — active pull

Lists currently-firing Monitoring alert incidents via the SecurityCenter / Monitoring API, caches them in `governance.cloud_monitoring_alerts`, and emits gap_reports findings for incidents tied to a registered workflow (via the `workflow_name` resource label).

| Incident state | Finding severity |
|---|---|
| `OPEN` | high |
| `CLOSED` | low |

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `cloud_monitoring_alert` | high | Currently-firing alert tied to a workflow |
| `cloud_monitoring_alert` | low | Recently-closed alert (audit trail) |

## Config

[`alert_policies.yaml`](../configuration.md). One policy per entry:

```yaml
policies:
  - display_name: cloud-run-job-failed-executions
    documentation: |
      Any Cloud Run job that completes with a non-zero exit code.
    severity: high
    enabled: true
    owner_team: sre_owners
    workflow_name: null      # optional — sets the threadable label
    conditions:
      - metric_type: run.googleapis.com/job/completed_execution_count
        filter: 'resource.label.job_name != ""'
        comparison: COMPARISON_GT
        threshold_value: 0
        duration_seconds: 60
        aggregation_alignment_period_seconds: 60
        aggregation_per_series_aligner: ALIGN_SUM
    notification_groups: [sre_owners]
```

Cross-validated at startup: unique `display_name`s; every policy has at least one condition.

### Schema fields

| Field | Type | Notes |
|---|---|---|
| `display_name` | str | Unique. Used as the resource ID. |
| `documentation` | str (markdown) | Surfaces in PagerDuty / Slack payloads. |
| `severity` | enum | `low | medium | high | critical`. Maps to the policy's UI metadata. |
| `enabled` | bool | Set to `false` to suspend without deleting. |
| `owner_team` | str | Required. |
| `conditions[].metric_type` | str | GCP metric type, e.g., `run.googleapis.com/job/completed_execution_count`. |
| `conditions[].filter` | str | Extra resource-label filter (PromQL-like). |
| `conditions[].comparison` | enum | `COMPARISON_GT | _LT | _GE | _LE`. |
| `conditions[].threshold_value` | float | The line. |
| `conditions[].duration_seconds` | int | How long the condition must hold. Default 300. |
| `conditions[].aggregation_per_series_aligner` | enum | `ALIGN_RATE | _MEAN | _MAX | _SUM | _PERCENTILE_95`. |

For anything more exotic — multi-condition policies, MQL, log-based alerts — author directly in the Monitoring console and leave that policy out of YAML.

## Tables read / written

**Reads** — Cloud Monitoring API (alert policies, incidents).

**Writes**

- `governance.cloud_monitoring_alerts` — cache of currently-firing incidents
- `governance.gap_reports` (scope='cloud_monitoring') — one finding per workflow-tagged incident

## Example finding

```json
{
  "scope": "cloud_monitoring",
  "scope_id": "my_workflow",
  "gap_kind": "cloud_monitoring_alert",
  "severity": "high",
  "details": {
    "as_of": "2026-05-13T14:05:00+00:00",
    "policy_name": "projects/my-project/alertPolicies/12345",
    "policy_display_name": "cloud-run-job-failed-executions",
    "metric_type": "run.googleapis.com/job/completed_execution_count",
    "threshold": 0,
    "observed": 2,
    "workflow_name": "my_workflow",
    "resource_type": "cloud_run_job"
  }
}
```

## When alerts don't link to a workflow

The sweep only emits findings for incidents whose resource labels include a `workflow_name` field. Project-level alerts (BQ slot saturation, network egress) get cached in `cloud_monitoring_alerts` but no per-workflow finding. Query the cache directly if you want a non-workflow view.

## Common questions

**Q: Why not just use GCP's native notification channels?**
Native channels work great for paging humans. This bot's job is different — it folds GCP alerts into the same `gap_reports` surface as anomaly / freshness / cost so the rollup, RCA, and MCP query tools can treat all signals uniformly. Use both: native channels for paging, this bot for batched governance review.

**Q: How does `sync_policies` handle drift from manual console edits?**
For unchanged-by-YAML fields, it leaves the live version alone. For fields covered by YAML (display_name, documentation, enabled), it overwrites with YAML's value. So if you tweak a threshold in the console and YAML still has the old threshold, the next `cloud_monitoring_sync` run reverts your console change. Treat YAML as the source of truth.

**Q: How do I delete a policy?**
Remove it from YAML. The v1 sync routine does NOT auto-delete from GCP (safety against accidental wipeouts). Manually delete via console or `gcloud alpha monitoring policies delete` once you're sure.

**Q: My policy is more complex than the schema supports.**
Author it in the GCP console and don't reference it in `alert_policies.yaml`. The sync routine doesn't touch unknown policies. You miss out on declarative drift detection but you get full Monitoring API expressiveness.
