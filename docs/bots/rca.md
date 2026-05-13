# rca — Gemini-narrated root-cause for exhausted retries

The rca bot is reactive. It picks up `approval_queue` items the retry orchestrator wrote, gathers recent context (events, findings, workflow metadata), asks the configured `LLMProvider` for a narrative explanation, and writes the result back to the queue row's `narrative` column.

**Module:** [`core/rca/`](../../src/sre_free_mcp/core/rca/)
**Task name:** `rca_tick`
**Default schedule:** `*/30 * * * *` (every 30 min)

## How one tick works

1. Query `approval_queue` for items with:
   - `action_kind IN ('retry_exhausted', ...)` (configurable)
   - `narrative IS NULL` (not yet processed)
   - `status='pending'`
2. For each item:
   - Parse `action_payload` for `workflow_name`.
   - Pull recent events (last 24h) + open findings for that workflow.
   - Build a structured prompt.
   - Call `llm.generate()`.
   - If the response contains the `NULL_LLM_SENTINEL` or the call raises, fall back to a deterministic 1-line summary.
   - UPDATE `approval_queue.narrative = ...` for that row.
3. Status stays `pending` — operator reviews and sets it to `approved`/`rejected` themselves.

## What output looks like

Narratives are 3-5 sentence plain-text summaries: what failed, what's known about why, what next step might unblock. Example:

```
The workflow ingest_users hit retry exhaustion at 03:42 UTC after 3 attempts
spaced 30s, 5m, 30m apart. The recent events show 3x failed_execution with
the same exit code (78), and the open finding workflow_failing_persistently
landed 18 minutes before exhaustion. The pattern suggests the upstream API
the workflow depends on is returning a stable 5xx — not a transient. Next
step: SELECT * FROM `project.raw.api_errors_log` WHERE workflow_name =
'ingest_users' AND occurred_at >= '2026-05-13' ORDER BY occurred_at DESC.
```

## When the bot no-ops

- `provider: 'none'` in `install.yaml` — `NullLLMProvider` is used; bot detects the sentinel and counts these as `skipped_null_llm`.
- An item already has a `narrative` — operator may have hand-edited; bot preserves it.
- `action_kind` not in the configured allowlist — bot skips non-RCA items.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `action_kinds` | `("retry_exhausted",)` | Tuple of action_kinds to process. Add `'high_severity_gap'` to also narrate rollup promotions. |
| `max_items_per_tick` | 10 | Cap on LLM calls per tick to control cost. |

## Per-item LLM exception isolation

If `llm.generate()` raises for one item (provider rate limit, transient network error), that item gets the deterministic fallback narrative and the bot moves on to the next. The bad item doesn't block the queue.

## Tables read / written

**Reads**

- `governance.approval_queue` — pending items
- `governance.events` — recent context
- `governance.gap_reports` — recent findings for the workflow

**Writes**

- `governance.approval_queue.narrative` (UPDATE)

## Example UPDATE

```sql
UPDATE `project.governance.approval_queue`
SET narrative = '...3-5 sentence narrative...'
WHERE id = '<approval_queue_row_id>'
```

The row stays `status='pending'` — narrative is information, not approval.

## Why output goes to `approval_queue` not `gap_reports`

RCAs are HITL artifacts (operator reads + confirms before any action). They live with the queue item they describe, not as new findings. The pipeline_health view doesn't change when an RCA lands; the operator's review surface does.

## Cost model

One LLM call per approval_queue item per tick (only if the item lacks a narrative). With Gemini Flash at ~$0.075/1M output tokens and ~500 tokens per RCA, you're looking at sub-cents per RCA. A noisy install producing 100 RCAs per day costs ~$0.01/day.

## Common questions

**Q: Why a separate `narrative` column instead of a new `rca` table?**
Because RCAs are 1:1 with the approval_queue items they describe. A separate table would require joins everywhere; storing the narrative inline is simpler.

**Q: What if I want the RCA to also call tools (read more data, query specific tables)?**
v1: just text-generation. The LLM has only what's in the prompt. v2 candidate: tool-using RCA that can call MCP tools mid-narrative.

**Q: Can I customize the prompt?**
Yes — `_PROMPT_TEMPLATE` is at the top of `audit.py`. Tweak for your team's conventions (mention specific runbooks, prefer certain phrasings, etc.).

**Q: How do I mark an item as approved or rejected?**
SQL UPDATE: `UPDATE approval_queue SET status = 'approved', resolution = 'investigated and fixed', resolved_by = 'alice', resolved_at = CURRENT_TIMESTAMP() WHERE id = '...'`. A future MCP tool (`approve(id, reason)`) is a v2 candidate.

**Q: Will this regenerate narratives on every tick?**
No — only for items where `narrative IS NULL`. Once written, it stays. To regenerate, clear the column: `UPDATE approval_queue SET narrative = NULL WHERE id = '...'`.
