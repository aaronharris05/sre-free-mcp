# incidents — first-class incident lifecycle

The incidents bot groups related findings + events into one operator-facing concept: an incident. It's the "what's broken right now" view that aggregates the raw signals from every other audit.

**Module:** [`core/incidents/`](../../src/sre_free_mcp/core/incidents/)
**Task name:** `incidents_tick`
**Default schedule:** `*/15 * * * *` (every 15 min)

## How one tick works

1. **Open new incidents.** For each workflow with one or more unresolved critical findings (or a retry-exhaustion event) and NO existing open incident, INSERT into `governance.incidents`.
2. **Close stale incidents.** For each open incident whose underlying workflow has been quiet (no new finding, no failed event) for `close_quiet_hours` (default 24h), set `closed_at` and `cause`.
3. Idempotent across ticks — a workflow with one open incident stays one open incident; re-running the tick is a no-op.

## Open rule

A new incident opens when:

- A workflow has ≥ `open_threshold` (default 1) **unresolved critical** finding(s) in `gap_reports`, AND
- That workflow has NO existing `incidents` row with `closed_at IS NULL`.

The default threshold is 1 — even one critical finding spawns an incident. Raise to 2-3 for noisier installs.

## Close rule

An open incident closes when:

- ALL its linked findings have `resolved_at IS NOT NULL`, OR
- The workflow has had no new failed events / unresolved findings for `close_quiet_hours` (default 24h).

Closing sets `closed_at = now()` and populates `cause` (e.g., "auto-closed: quiet 24h" or "auto-closed: all linked findings resolved"). The RCA bot may later overwrite `cause` with its narrative.

## Schema — the `incidents` table

| Column | Notes |
|---|---|
| `id` | UUID4 |
| `workflow_name` | Nullable — some incidents span scopes |
| `scope` | Mirrors `gap_reports.scope` when single-scope |
| `opened_at` | Set on INSERT |
| `closed_at` | NULL until closed |
| `severity` | Worst severity across linked findings |
| `summary` | One-liner pulled from the worst finding's `details.rule` |
| `cause` | Filled by RCA bot or close logic |
| `linked_finding_ids` | ARRAY<STRING> — `gap_reports.id` values |
| `linked_event_ids` | ARRAY<STRING> — `events.id` values |
| `rca_approval_id` | Pointer into `approval_queue` when RCA generated |

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `open_threshold` | 1 | Critical-finding count to trigger open |
| `close_quiet_hours` | 24 | Quiet window for auto-close |

## Interaction with other bots

- **retry** writes `events` rows that the incident logic considers. A workflow with sustained retry exhaustions opens an incident.
- **rca** consumes `approval_queue` items written by the retry orchestrator. Its narrative gets attached to the incident via `rca_approval_id`.
- **rollup** counts open incidents in its weekly summary.

## Tables read / written

**Reads**

- `governance.gap_reports` — unresolved critical findings
- `governance.events` — recent failures
- `governance.incidents` — to avoid duplicate opens

**Writes**

- `governance.incidents` — INSERT for new opens; UPDATE for closes

## Example incidents row

```json
{
  "id": "uuid",
  "workflow_name": "ingest_users",
  "scope": "workflow",
  "opened_at": "2026-05-12T03:45:00+00:00",
  "closed_at": null,
  "severity": "critical",
  "summary": "Last 3 executions all completed FAILED status",
  "cause": null,
  "linked_finding_ids": ["gap_uuid_1", "gap_uuid_2"],
  "linked_event_ids": [],
  "rca_approval_id": null
}
```

## Common questions

**Q: Why isn't this just a query against `gap_reports`?**
Because incidents have lifecycle. A finding can be unresolved for days while an operator works on it; an incident captures "open since X, RCA at Y, closed at Z." The two tables serve different purposes — findings are atomic signals; incidents are operator-facing units of work.

**Q: How do I manually open an incident?**
INSERT a row directly into `governance.incidents`. The bot is additive — it won't disturb hand-created rows.

**Q: How do I manually close one?**
`UPDATE governance.incidents SET closed_at = CURRENT_TIMESTAMP(), cause = 'manually closed' WHERE id = '...'`.

**Q: An incident is open but I've fixed the underlying issue. Will it auto-close?**
Yes — once all linked findings have `resolved_at` set (mark them resolved with a SQL UPDATE), or once 24 hours have passed with no new failures.
