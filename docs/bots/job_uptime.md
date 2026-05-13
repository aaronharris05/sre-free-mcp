# job_uptime — detect workflows that should have run but didn't

The job_uptime bot is the silent-failure detector. Every day it cross-references the workflow registry against Cloud Run executions and Cloud Scheduler state, applying five rules to flag workflows that aren't behaving as their registration promises.

**Module:** [`core/job_uptime/`](../../src/sre_free_mcp/core/job_uptime/)
**Task name:** `job_uptime_sweep`
**Default schedule:** `0 6 * * *` (daily 06:00 UTC)

## How one sweep works

1. Read every active row from `governance.workflows`.
2. For each workflow, resolve the Cloud Run job via the `workflow_name` label.
3. Pull the last 5 execution statuses from the Cloud Run Executions API.
4. Resolve Cloud Scheduler pause state for any workflow with `trigger_kind='scheduler'`.
5. Apply five rules per workflow.
6. Bulk-write findings to `gap_reports` with `scope='workflow'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `workflow_paused` | high | Cloud Scheduler job state ≠ ENABLED |
| `workflow_missed_run` | high | `age(last_execution) > cadence + grace_hours` (default 2h grace) |
| `workflow_failing_persistently` | critical | Last `failing_streak` executions all completed with FAILED status |
| `workflow_no_scheduler` | high | `trigger_kind=scheduler` but no matching `<job>-trigger` in Cloud Scheduler |
| `workflow_stale_registration` | medium | Workflow has a cron but no execution has ever run |

## Cron parsing — what's supported

The missed-run rule needs to map cron to an expected period. The bundled parser supports:

- `*/N * * * *` — every N minutes
- `M * * * *` — hourly at minute M
- `M H * * *` — daily at HH:MM
- `M H * * D` — weekly on day D at HH:MM

Anything else (`*/15 9-17 * * 1-5` etc.) returns `None` and the missed-run rule fails open (no finding). Add a TODO if your fleet uses exotic crons; the parser is at the top of `checks.py` and is the cleanest place to extend.

## Config

No YAML — the bot consumes `governance.workflows` directly. Pre-conditions for clean output:

1. Each workflow you care about is registered with `status='active'`.
2. The Cloud Run job carrying it has a `workflow_name` label matching the workflows row.
3. Cloud Scheduler jobs follow the `<job_name>-trigger` naming convention.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `grace_hours` | 2.0 | How far past the expected cadence the missed-run rule tolerates |
| `failing_streak` | 3 | How many consecutive failures fire `workflow_failing_persistently` |
| `write` | `True` | Set False for dry-runs |

## Tables read / written

**Reads**

- `governance.workflows` — active registrations
- Cloud Run Jobs API + Executions API — recent run history
- Cloud Scheduler API — pause state

**Writes**

- `governance.gap_reports` (scope='workflow')

## Example findings

```json
{
  "scope": "workflow",
  "scope_id": "ingest_users",
  "gap_kind": "workflow_missed_run",
  "severity": "high",
  "details": {
    "workflow": "ingest_users",
    "cron": "0 4 * * *",
    "expected_cadence_hours": 24,
    "last_execution_at": "2026-05-11T04:00:12+00:00",
    "age_hours": 50.2
  }
}
```

```json
{
  "scope": "workflow",
  "scope_id": "ingest_orders",
  "gap_kind": "workflow_failing_persistently",
  "severity": "critical",
  "details": {
    "workflow": "ingest_orders",
    "cron": "*/15 * * * *",
    "failing_streak": 3,
    "recent_statuses": ["failed", "failed", "failed", "ok", "ok"]
  }
}
```

## Interaction with the retry bot

`workflow_missed_run` and `workflow_failing_persistently` findings flip a workflow to red in `pipeline_health_v1`. The [retry](retry.md) bot watches that view and reacts on its next tick (within 5 min) — assuming the workflow is idempotent and the breaker isn't open.

The job_uptime bot is the "what's wrong" eye; the retry bot is the "fix it" hand.

## Common questions

**Q: My workflow is event-triggered, not scheduled. Why is it flagged stale?**
`workflow_stale_registration` only fires when `cron IS NOT NULL`. Set the workflow's cron to NULL (or use `trigger_kind='event'`/`'manual'`) and the rule skips it.

**Q: Why does this need the `workflow_name` label on Cloud Run jobs?**
Because the workflow name is what links a `governance.workflows` row to the actual deployed job. Without the label the bot can't tell which Cloud Run job corresponds to which workflow — and the retry orchestrator can't either.

**Q: Can I customize the failing-streak threshold per workflow?**
Not via YAML today. The `failing_streak` parameter is global to the sweep. Per-workflow tuning is on the v2 roadmap.

**Q: What happens if the Cloud Run API call fails?**
The bot catches and logs the error per-workflow; that workflow gets `None` for its execution history and rules that require history don't fire. Other workflows continue.
