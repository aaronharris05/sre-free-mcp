# Bots reference

Sixteen audit bots + one retry orchestrator. Each has its own doc page covering rules, config, and example findings.

## Audit bots (write to `gap_reports`)

| Bot | scope | Schedule (default) | Reads | Config |
|---|---|---|---|---|
| [anomaly](anomaly.md) | `data` | daily 04:00 UTC | configured BQ tables | `anomaly_targets.yaml` |
| [job_uptime](job_uptime.md) | `workflow` | daily 06:00 UTC | `workflows` + Cloud Run executions + Cloud Scheduler | — |
| [freshness](freshness.md) | `freshness` | daily 04:30 UTC | `INFORMATION_SCHEMA.TABLE_STORAGE` | `freshness_targets.yaml` |
| [cost](cost.md) | `cost` | daily 05:00 UTC | `cost_daily` (customer-side ETL fills it) | — |
| [cloud_monitoring](cloud_monitoring.md) | `cloud_monitoring` | every 15 min | Cloud Monitoring API | `alert_policies.yaml` |
| [data_catalog](data_catalog.md) | `catalog` | weekly Mon 07:00 UTC | `workflows` | — |
| [ai_gov](ai_gov.md) | `ai_gov` | weekly Mon 07:00 UTC | `workflows` | — |
| [dependency](dependency.md) | `dependency` | (disabled by default) | local pyproject.toml / requirements.txt | env var |
| [scm](scm.md) | `scm` | (disabled by default) | GitHub REST API | env var |
| [test_coverage](test_coverage.md) | `test_coverage` | (disabled by default) | local coverage.xml | env var |
| [secret_iam](secret_iam.md) | `secret_iam` | daily 07:00 UTC | Secret Manager + project IAM | — |
| [security](security.md) | `security` | (disabled by default) | SCC findings (org-level) | `install.organization_id` |
| [pii](pii.md) | `pii` | (disabled by default) | Cloud DLP inspect API | `pii_targets.yaml` |
| [llm_safety](llm_safety.md) | `llm_safety` | (disabled by default) | configured LLM provider | code-level |

## Reactive bots (consume findings + events)

| Bot | What it produces | Schedule |
|---|---|---|
| [retry](retry.md) | Cloud Run job re-executions + `events` + `approval_queue` (on exhaust) | every 5 min |
| [incidents](incidents.md) | `incidents` rows (open / close) | every 15 min |
| [rca](rca.md) | LLM narratives written back to `approval_queue.narrative` | every 30 min |
| [rollup](rollup.md) | Weekly summary email + `approval_queue` promotions | weekly Mon 13:00 UTC |

## The shape of every bot

```
core/<bot>/
├── __init__.py
├── checks.py     # pure-logic rules — no I/O
├── audit.py      # sweep() — pulls state, calls evaluate, writes findings
└── (targets.py)  # pydantic config schema if applicable
```

All audit bots emit `Finding` records via the shared `core.findings.write_findings` helper:

```python
Finding(
    scope="<bot's discriminator>",       # 'data' | 'cost' | 'freshness' | ...
    scope_id="<what within the scope>",  # "table:column" / "workflow_name" / ...
    gap_kind="<machine-readable name>",  # 'anomaly_zscore' / 'unrotated_secret' / ...
    severity="<one of>",                 # 'low' | 'medium' | 'high' | 'critical'
    details={ ... },                     # free-form JSON
)
```

Findings land in `governance.gap_reports`. The `incidents` and `rollup` bots consume them; the MCP server's `recent_findings()` tool surfaces them on demand.

## Adding a new bot

See [extending.md](../extending.md) for the full walkthrough.
