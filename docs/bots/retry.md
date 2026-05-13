# retry — retry orchestrator with circuit breaker

The retry bot is the reactive heart of sre-free-mcp. Every 5 minutes it scans `pipeline_health_v1` for workflows in a red state with transient-looking failures, applies a per-workflow retry policy + circuit breaker, and triggers Cloud Run job re-executions through the Admin API. After `max_attempts` without success it enqueues an item in `approval_queue` for human + LLM review.

**Module:** [`core/retry/`](../../src/sre_free_mcp/core/retry/)
**Task name:** `retry_tick`
**Default schedule:** `*/5 * * * *`

## What one tick does

1. Read red rows from `pipeline_health_v1` whose worst gap_kind is transient-by-default (`workflow_missed_run` or `workflow_failing_persistently`).
2. For each candidate:
   - Look up the per-workflow policy from `retry_policies.yaml` (defaults if absent).
   - Veto if `workflows.idempotent` is null/false (unless the policy waives `require_idempotent`).
   - Veto if the circuit breaker is open (`>= breaker_threshold` retry events in `breaker_window_min`).
   - Wait if the previous attempt is younger than the current backoff.
   - Otherwise resolve the Cloud Run job via the `workflow_name` label and trigger a re-execution.
3. Write one `events` row per decision (including vetoes — counts toward the breaker).
4. After `max_attempts` of unsuccessful retries, write `outcome='exhausted'` and INSERT a row into `approval_queue` with `action_kind='retry_exhausted'` so the [rca](rca.md) bot can pick it up.

## Outcomes a tick can produce

Each candidate produces one of these `events.payload.outcome` values:

| Outcome | What it means |
|---|---|
| `not_idempotent` | Veto — workflow's `idempotent` flag is null/false |
| `breaker_open` | Veto — circuit breaker is open for this workflow |
| `waiting` | Backoff window not yet satisfied. No event row written; just a decision log. |
| `ok` | Successfully triggered a Cloud Run job re-execution |
| `trigger_failed` | Job re-execution API call failed |
| `exhausted` | Out of attempts; RCA queue item written |
| `no_job` | Couldn't resolve the workflow to a Cloud Run job via labels |
| `error` | Unexpected exception during decision |

## Config

[`retry_policies.yaml`](../configuration.md#retry_policiesyaml). Default policy if a workflow has no override:

```python
RetryPolicy(
    max_attempts=3,
    backoff_seconds=(30, 300, 1800),    # 30s, 5m, 30m
    breaker_window_min=60,
    breaker_threshold=5,
    require_idempotent=True,
)
```

## Circuit breaker

The breaker has three states, computed fresh each tick — there's no persisted state machine:

- `closed` — fewer than `breaker_threshold` retry attempts in the last `breaker_window_min` minutes. New retries allowed.
- `open` — at or above threshold, with at least one attempt in the last 30 minutes. New retries vetoed.
- `half_open` — at or above threshold but quiet for the last 30 minutes. One trial allowed; if it succeeds the breaker re-closes naturally as the window slides.

The breaker counts every retry event (not just successful triggers), so a workflow that keeps getting vetoed by other rules still trips the breaker — preventing infinite veto loops.

### Fail-open on BQ errors

If the breaker query itself fails (BigQuery hiccup), `state_for()` returns `'closed'`. Better to allow a possibly-redundant retry than stall the fleet on a BQ blip.

## Required workflow metadata

For the retry orchestrator to fire on a workflow, the workflow must:

1. Be registered in `governance.workflows` with `status='active'`.
2. Have `idempotent=TRUE` (unless your policy waives `require_idempotent`).
3. Have a Cloud Run job in the same region with a `workflow_name` label matching the workflows.name value. The orchestrator uses this label to resolve which job to re-execute.

```hcl
# Example Cloud Run job label
labels = {
  workflow_name = "my_workflow"
}
```

## Tables read / written

**Reads**

- `governance.pipeline_health_v1` — candidate selection
- `governance.workflows` — idempotent flag
- `governance.events` — prior attempts + breaker accounting

**Writes**

- `governance.events` — one row per decision
- `governance.approval_queue` — one row on `exhausted` outcomes

## Tunable knobs

`tick()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `project` | from env | Required. |
| `region` | from env | Required. Where the Cloud Run jobs live. |
| `bq_client` | live BQ client | Stubbable in tests. |
| `tables` | `GovernanceTables()` | Customize for non-default dataset names. |
| `now` | `datetime.now(UTC)` | Inject a clock for tests. |

Per-workflow policy overrides live in `retry_policies.yaml`.

## Example decision payload

```json
{
  "event": "sre_retry_tick",
  "run_id": "5f...",
  "generated_at": "2026-05-13T14:05:00+00:00",
  "totals": {"ok": 2, "waiting": 1, "breaker_open": 1, "not_idempotent": 0, "exhausted": 0, "candidates": 4},
  "decisions": [
    {"workflow_name": "ingest_users", "outcome": "ok", "attempt_number": 1, "backoff_used": 30, "breaker": "closed"},
    {"workflow_name": "ingest_orders", "outcome": "waiting", "wait_remaining_s": 240},
    {"workflow_name": "ingest_logs",   "outcome": "breaker_open"},
    {"workflow_name": "ingest_inv",    "outcome": "ok", "attempt_number": 2, "backoff_used": 300, "breaker": "closed"}
  ]
}
```

## Common questions

**Q: Why is retry split from RCA?**
The retry orchestrator's job is mechanical: schedule re-executions when the policy allows, give up after `max_attempts`, enqueue an `approval_queue` item on exhaustion. Generating the human-readable root-cause narrative is a separate concern handled by the [rca](rca.md) bot — different cadence (every 30 min vs every 5 min), different dependency surface (LLM provider), different output channel (queue narrative vs `events`).

**Q: How do I disable retries for a specific workflow?**
Set `max_attempts: 1` and `backoff_seconds: []` in `retry_policies.yaml`. The orchestrator will still see it as a candidate but exhaust immediately and enqueue for RCA without trying.

**Q: How do I retry a non-idempotent workflow?**
Override with `require_idempotent: false` in `retry_policies.yaml`. Strongly NOT recommended — you may corrupt downstream state. Better: fix the workflow to be idempotent.

**Q: Where does the retry actually run?**
Same Cloud Run job that the workflow originally ran in. The orchestrator only triggers a new execution via the Admin API; it doesn't run the workflow logic inline.
