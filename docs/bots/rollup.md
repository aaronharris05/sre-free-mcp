# rollup — weekly cross-bot summary

The rollup bot is the operator's Monday-morning email. It aggregates every active audit bot's output into one summary (counts by severity + scope, open incidents, pending approvals, top-N findings) and ships it via the configured `EmailSender`. It also optionally promotes high-severity unresolved findings into the `approval_queue` so they don't get lost.

**Module:** [`core/rollup/`](../../src/sre_free_mcp/core/rollup/)
**Task name:** `rollup`
**Default schedule:** `0 13 * * 1` (weekly Monday 13:00 UTC)

## How one tick works

1. Aggregate counts from `gap_reports` (open findings by scope + severity), `incidents` (open incident count), `approval_queue` (pending review count).
2. Build a `RollupSummary` with top-N findings sorted by severity then recency.
3. (Optional) Promote high/critical unresolved findings into `approval_queue` with deterministic IDs (so re-running the sweep doesn't double-promote).
4. (Optional) Send via the configured `EmailSender`. Email-send errors are caught — a flaky SMTP doesn't break the audit.
5. Return a `RollupSummary` regardless of whether email was sent.

## Outputs

```python
@dataclass(frozen=True)
class RollupSummary:
    generated_at: datetime
    window_days: int
    open_findings_total: int
    open_findings_by_scope: dict[str, int]
    open_findings_by_severity: dict[str, int]
    open_incidents: int
    pending_approvals: int
    top_findings: list[dict[str, Any]]
```

The MCP server's `run_task("rollup")` returns this for on-demand inspection.

## Email format

Two bodies — plain text and HTML. The text body is a list-per-section format suitable for log viewers; the HTML body has count tables and a properly-escaped top-findings table.

The email subject summarizes the state:

- `[Rollup] All clear — 2026-05-13` (zero findings, zero incidents)
- `[Rollup] 12 open findings, 2 open incidents — 2026-05-13`
- `[Rollup] 4 high / 12 open findings, 2 open incidents — 2026-05-13` (when high-severity present)

## Promotion to approval_queue

When `promote_high_severity=True` (default), every unresolved finding with severity `'high'` or `'critical'` gets an `approval_queue` row inserted with `action_kind='high_severity_gap'`. The row's deterministic UUID5 ID is computed from the finding ID, so re-running the rollup never double-inserts.

This is how high-severity findings flow into operator review: rollup promotes → RCA bot writes narrative → operator approves/rejects.

## Tunable knobs

`sweep()` exposes:

| Arg | Default | Notes |
|---|---|---|
| `window_days` | 7 | Lookback window for "recent" findings in the summary |
| `top_n` | 5 | Number of top findings to include in the email body |
| `promote_high_severity` | True | When False, no `approval_queue` writes |
| `promote_action_kind` | "high_severity_gap" | Customize the action_kind on promotions |

## Tables read / written

**Reads**

- `governance.gap_reports`
- `governance.incidents`
- `governance.approval_queue`

**Writes**

- `governance.approval_queue` — promotion INSERTs (idempotent via deterministic UUID5)

## Example summary

```json
{
  "generated_at": "2026-05-13T13:00:00+00:00",
  "window_days": 7,
  "open_findings_total": 14,
  "open_findings_by_scope": {
    "workflow": 4,
    "freshness": 6,
    "cost": 2,
    "ai_gov": 2
  },
  "open_findings_by_severity": {
    "critical": 1,
    "high": 5,
    "medium": 7,
    "low": 1
  },
  "open_incidents": 2,
  "pending_approvals": 3,
  "top_findings": [
    {
      "severity": "critical",
      "scope": "workflow",
      "scope_id": "ingest_orders",
      "gap_kind": "workflow_failing_persistently"
    },
    {
      "severity": "high",
      "scope": "freshness",
      "scope_id": "raw.api_latency_hourly",
      "gap_kind": "table_stale"
    }
  ]
}
```

## NullEmailSender behavior

When `email.smtp_password_secret` is empty, the runner uses `NullEmailSender`, which records the message but doesn't transmit. `email_sent=True` is returned because the sender succeeded — just not over the wire. The operator can find the message in Cloud Logging if needed.

## Common questions

**Q: Why weekly instead of daily?**
Weekly gives the operator a Monday-morning roll-call rhythm without daily noise. Critical findings still surface via `incidents` (every 15 min) and `approval_queue` promotions (every weekly rollup). Run more often if you want — bump the cron to daily or even hourly; the cost is one or two queries and one email per run.

**Q: How do I add a different recipient group?**
The runner currently emails `recipients.lookup("governance_owners")`. To split by team — e.g., per-scope summaries to per-team groups — edit `runner/tasks.py::rollup` to call `sweep()` once per team with the right recipient list.

**Q: Can I get a Slack notification instead?**
Yes — write a `SlackEmailSender` (subclass `EmailSender`) that posts to a webhook instead of sending SMTP. Pass it to `sweep()` instead of the configured default.

**Q: The email body has too many findings — how do I trim?**
Lower `top_n`. The full set still lives in `gap_reports`; the email just shows the top N by severity + recency.

**Q: Is there a "rollup landed" event in `events`?**
Not in v1. The rollup writes only to `approval_queue`. Adding an event row for audit-trail purposes is a v2 candidate.
