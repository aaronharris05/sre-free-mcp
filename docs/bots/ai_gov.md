# ai_gov — policy compliance audit

The ai_gov bot audits every workflow row for the metadata most orgs require around AI / data-handling artifacts: business purpose, policy references, owner team. It's the "policy compliance" half of the catalog audit pair — [data_catalog](data_catalog.md) handles the "structural" half.

**Module:** [`core/ai_gov/`](../../src/sre_free_mcp/core/ai_gov/)
**Task name:** `ai_gov_audit`
**Default schedule:** `0 7 * * 1` (weekly Monday 07:00 UTC)

## How one audit works

1. Read every `status='active'` row from `governance.workflows`.
2. Apply three policy-compliance rules.
3. Bulk-write findings to `gap_reports` with `scope='ai_gov'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `missing_business_purpose` | high | `business_purpose IS NULL` or empty |
| `business_purpose_too_terse` | medium | `len(business_purpose.strip()) < business_purpose_min_chars` (default 30) |
| `missing_policy_refs` | medium | `len(policy_refs) == 0` AND `require_policy_refs=True` (default False) |

## Config

No YAML — the bot consumes the registry directly. Tuning is at the `evaluate()` call.

## Tunable knobs

`sweep()` (via the runner task) exposes:

| Arg | Default | Notes |
|---|---|---|
| `business_purpose_min_chars` | 30 | Below this, the purpose looks like a placeholder |
| `require_policy_refs` | False | Off by default; enable for regulated installs |

## Tables read / written

**Reads** — `governance.workflows`.

**Writes** — `governance.gap_reports` (scope='ai_gov').

## Example finding

```json
{
  "scope": "ai_gov",
  "scope_id": "ingest_users",
  "gap_kind": "business_purpose_too_terse",
  "severity": "medium",
  "details": {
    "workflow": "ingest_users",
    "business_purpose": "TODO",
    "min_chars": 30,
    "actual_chars": 4,
    "rule": "business_purpose < 30 chars; likely placeholder"
  }
}
```

## When to enable `require_policy_refs`

`policy_refs` is an array of policy IDs the workflow attests to (e.g., `["policy-01-2.4", "policy-19-PII"]`). Most orgs don't need this until they're under SOC 2 / HIPAA / GDPR audit pressure. Then enabling it forces every registered workflow to cite which policies it complies with — useful for audit walkthroughs.

To enable: edit `runner/tasks.py::ai_gov_audit` to pass `require_policy_refs=True` to `sweep()` (or, in v2, surface as a config option).

## Common questions

**Q: What counts as a "good" business_purpose?**
Subjective, but practically: ~50-200 chars, plain English, says WHAT the workflow does and WHO benefits. "Daily customer rollup against authentication logs for the finance team's churn dashboard" is good. "data sync" is not.

**Q: Why is this separate from data_catalog?**
Two reasons: (a) the rules apply different severity to different fields based on different concerns — data_catalog enforces mechanics, ai_gov enforces documentation; (b) some orgs care intensely about AI-governance compliance and others don't — splitting makes the two enable/disable independently.

**Q: Does this care which LLM / model the workflow uses?**
Not in v1. A future "model_provenance" audit could check that every workflow with `policy_refs: ["model_provenance"]` has the model name + version recorded in `details`. For now it's just business_purpose + owner_team + optional policy_refs.

**Q: Are there opinions about what `policy_refs` should reference?**
Each install defines its own policy library. The bot doesn't validate the IDs against a master list (v2 candidate). Whatever ID scheme your org uses — `POL-001`, `policy-01-2.4`, `compliance-soc2-cc1.1` — just be consistent.
