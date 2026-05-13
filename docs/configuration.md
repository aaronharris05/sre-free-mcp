# Configuration

Every knob sre-free-mcp exposes lives in `config/`. Seven YAML files, each optional except `install.yaml`. Missing files fall back to bundled `*.example.yaml`. Schemas are validated with pydantic at startup; bad config crashes the runner with a useful error rather than producing wrong behavior at 04:00 UTC.

## File overview

| File | Required? | Purpose |
|---|---|---|
| `install.yaml` | Yes | Project, region, dataset, email, LLM provider |
| `retry_policies.yaml` | No | Per-workflow retry overrides |
| `recipients.yaml` | No | Team groups → email lists |
| `anomaly_targets.yaml` | No | Tables + metric columns to scan for outliers |
| `freshness_targets.yaml` | No | Tables with refresh SLAs |
| `alert_policies.yaml` | No | Declarative GCP Cloud Monitoring policies |
| `pii_targets.yaml` | No | Tables to inspect with Cloud DLP |

If a file is missing, the runner falls back to the bundled `<name>.example.yaml`. Three env vars override `install.yaml`'s top-level fields even when present:

- `GCP_PROJECT` → `install.project_id`
- `GCP_REGION` → `install.region`
- `SRE_GOVERNANCE_DATASET` → `install.governance_dataset`
- `GCP_ORGANIZATION_ID` → `install.organization_id`

The Terraform module sets all four for you, so the bundled example install.yaml can be used as-is in any project.

## `install.yaml`

```yaml
project_id: my-gcp-project
region: us-central1
governance_dataset: governance      # rename if you have an existing 'governance' dataset
organization_id: ""                  # required by 'security' audit (SCC); empty disables it

email:
  from_address: sre-alerts@example.com
  smtp_host: smtp.gmail.com
  smtp_port: 587
  smtp_username: sre-alerts@example.com
  smtp_password_secret: sre-smtp-password   # Secret Manager secret NAME, not the value

llm:
  provider: gemini                          # gemini | anthropic | openai | none
  model: gemini-2.0-flash
  api_key_secret: sre-llm-api-key
```

| Field | Type | Notes |
|---|---|---|
| `project_id` | str | Required. Where this install lives. |
| `region` | str | Default `us-central1`. |
| `governance_dataset` | str | BQ dataset name. Default `governance`. Use a different name if you already have one. |
| `organization_id` | str | GCP org for SCC findings. Empty disables the `security` bot. |
| `email.from_address` | str | The "From" header on outgoing email. |
| `email.smtp_host` / `_port` / `_username` | str / int / str | Standard SMTP creds. |
| `email.smtp_password_secret` | str | Name of a Secret Manager secret holding the SMTP password. Empty falls back to `NullEmailSender` (logs, doesn't send). |
| `llm.provider` | enum | `gemini` (built-in), `anthropic` / `openai` (reserved — bring your own subclass), `none` (no LLM). |
| `llm.model` | str | Model name within the provider. |
| `llm.api_key_secret` | str | Secret Manager secret holding the API key. |

## `retry_policies.yaml`

Override per-workflow retry behavior. Workflows not listed use the safe defaults below.

```yaml
policies:
  - workflow_name: example_quota_limited_ingest
    max_attempts: 2
    backoff_seconds: [60, 600]

  - workflow_name: example_llm_heavy_workflow
    max_attempts: 2
    backoff_seconds: [120, 1800]

  - workflow_name: example_self_retrying_workflow
    max_attempts: 1
    backoff_seconds: []
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `workflow_name` | str | — | Must match a row in `governance.workflows.name`. |
| `max_attempts` | int | 3 | Total attempts including the original. |
| `backoff_seconds` | list[int] | [30, 300, 1800] | Wait before each retry. Clamped to last element if shorter than `max_attempts - 1`. |
| `breaker_window_min` | int | 60 | Window the circuit breaker counts retries over. |
| `breaker_threshold` | int | 5 | Open the breaker at this count. |
| `require_idempotent` | bool | true | Skip retries when `workflows.idempotent` is null/false. |

See [bots/retry.md](bots/retry.md) for the full state machine.

## `recipients.yaml`

Team groups → email lists. Audit bots route findings by `owner_team`; this file resolves the actual addresses.

```yaml
groups:
  governance_owners:
    - ops@example.com
  data_owners:
    - data-team@example.com
  trader_owners:
    - trader@example.com
  finance_owners:
    - finance@example.com
```

Required group: `governance_owners` is the fallback when a finding can't be routed to a more specific team. Add it even if your other teams are empty.

A target referencing an undeclared group fails validation at config load (so you catch the typo before 04:00 UTC).

## `anomaly_targets.yaml`

```yaml
targets:
  - table: example_dataset.api_latency_hourly
    metric_column: latency_p95_ms
    timestamp_column: ts
    lookback_days: 14
    severity_red_z: 5.0
    severity_yellow_z: 3.5
    owner_team: data_owners
    purpose: API latency p95 outlier (data quality)

  - table: example_dataset.request_volume_hourly
    metric_column: mw
    timestamp_column: ts
    lookback_days: 30
    severity_red_z: 6.0
    severity_yellow_z: 4.0
    owner_team: trader_owners
    purpose: Request volume outlier (business signal)
    not_yet_built: true
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `table` | str | — | Dataset-qualified BQ table. Project is added at runtime. |
| `metric_column` | str | — | Numeric column to score. |
| `timestamp_column` | str | — | TIMESTAMP / DATE column used to window the lookback. |
| `lookback_days` | int | — | How far back to fit the detector. >0. |
| `severity_red_z` | float | — | \|z\| ≥ this fires high severity. |
| `severity_yellow_z` | float | — | \|z\| ≥ this fires medium severity. Must be ≤ red. |
| `owner_team` | str | — | Must match a recipients group. |
| `purpose` | str | "" | One-liner shown in the email body. |
| `not_yet_built` | bool | false | When true, the sweep skips this target with a log line. |

See [bots/anomaly.md](bots/anomaly.md).

## `freshness_targets.yaml`

```yaml
targets:
  - dataset: raw
    table: api_latency_hourly
    expected_cadence_hours: 1.5
    owner_team: data_owners
    purpose: API latency metrics hourly ingest

  - dataset: curated
    table: active_users_daily
    expected_cadence_hours: 26
    owner_team: finance_owners
    purpose: Daily active-users rollup
    not_yet_built: true
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `dataset` | str | — | BQ dataset (no project prefix). |
| `table` | str | — | BQ table name. |
| `expected_cadence_hours` | float | — | How often the table is expected to refresh. >0. |
| `owner_team` | str | — | Must match a recipients group. |
| `purpose` | str | "" | One-liner. |
| `not_yet_built` | bool | false | Skip flag. |

See [bots/freshness.md](bots/freshness.md).

## `alert_policies.yaml` (optional — Cloud Monitoring sync)

Declare GCP Cloud Monitoring alert policies in YAML; the `cloud_monitoring_sync` task reconciles them against the API.

```yaml
policies:
  - display_name: cloud-run-job-failed-executions
    documentation: |
      Any Cloud Run job that completes with a non-zero exit code.
    severity: high
    enabled: true
    owner_team: sre_owners
    conditions:
      - metric_type: run.googleapis.com/job/completed_execution_count
        filter: 'resource.label.job_name != ""'
        comparison: COMPARISON_GT
        threshold_value: 0
        duration_seconds: 60
        aggregation_per_series_aligner: ALIGN_SUM
    notification_groups: [sre_owners]
```

See [bots/cloud_monitoring.md](bots/cloud_monitoring.md) for the full field reference + supported aligners/comparisons.

## `pii_targets.yaml` (optional — Cloud DLP)

```yaml
targets:
  - dataset: customers
    table: users
    sample_rows: 1000
    info_types: [EMAIL_ADDRESS, PHONE_NUMBER, US_SOCIAL_SECURITY_NUMBER]
```

See [bots/pii.md](bots/pii.md).

## Config overrides in production

The bundled `*.example.yaml` files ship inside the container at `/app/config-examples` and are copied to `/app/config` at startup. To use different configs in production, three options:

### Option 1 — rebuild the image with your configs

Fork the repo, edit `config/*.yaml` directly, rebuild:

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/sre-free-mcp:latest --project=$PROJECT_ID
```

Simple. Downside: config is baked in; updating it requires a new build.

### Option 2 — mount via Secret Manager (`config_secret_name`)

The Terraform module supports mounting a Secret Manager secret as the config volume:

```hcl
module "sre" {
  source             = "../../modules/sre"
  project_id         = "my-project"
  container_image    = "gcr.io/my-project/sre-free-mcp:latest"
  config_secret_name = "sre-mcp-install-yaml"   # holds install.yaml content
}
```

Then create the secret manually:

```bash
gcloud secrets create sre-mcp-install-yaml --project=my-project
gcloud secrets versions add sre-mcp-install-yaml \
  --data-file=./my-install.yaml --project=my-project
```

The current implementation mounts a single file at `/app/config/install.yaml`. Multi-file mounting (one secret per YAML, or a tarball) is on the roadmap.

### Option 3 — env var overrides

For the most-common values, env vars set on the Cloud Run service/job take precedence over `install.yaml`:

| Env var | Overrides |
|---|---|
| `GCP_PROJECT` | `install.project_id` |
| `GCP_REGION` | `install.region` |
| `SRE_GOVERNANCE_DATASET` | `install.governance_dataset` |
| `GCP_ORGANIZATION_ID` | `install.organization_id` |
| `SRE_CONFIG_DIR` | The whole config directory (default `/app/config`) |

The Terraform module sets the first four for you. To override others, add `env` blocks to the service / job in your own fork of the module.

## Cross-validation

The config loader runs a small set of cross-checks at startup. Validation failures crash the runner with the exact field reference.

- **Anomaly target owner_team** must appear in `recipients.groups`.
- **Freshness target owner_team** must appear in `recipients.groups`.
- **No duplicate `(table, metric_column)` pairs** in `anomaly_targets`.
- **No duplicate `(dataset, table)` pairs** in `freshness_targets`.
- **No duplicate `display_name`s** in `alert_policies`.
- **Anomaly target severity_red_z ≥ severity_yellow_z** (otherwise a "yellow" finding would never escalate to "red").
- **Alert policy must have ≥1 condition.**
