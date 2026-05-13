# sre-free-mcp

<!-- mcp-name: io.github.aaronharris05/sre-free-mcp -->

Self-hosted SRE platform for GCP, callable by any AI agent via [Model Context Protocol](https://modelcontextprotocol.io) server. Anomaly detection, retry orchestration with circuit breakers, cost + PII + IAM audits — Cloud Run + BigQuery, Terraform-deployed.

Drop it into a GCP project and you get sixteen audit bots, a retry orchestrator with circuit breaker, an incident lifecycle, and an MCP surface any AI agent (Claude, Cursor, custom) can call — all running on Cloud Run, writing to BigQuery, configured by YAML.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-473%20passing-brightgreen)](#)

```
┌────────────────────────────────────────────────────────────────────────────┐
│   Customer GCP Project                                                     │
│                                                                            │
│   ┌──────────────────┐    HTTPS/SSE          ┌──────────────────────────┐  │
│   │ MCP client       │───── + IAM token ────▶│ Cloud Run service        │  │
│   │ (Claude Desktop, │                       │ sre-mcp-server (9 tools) │  │
│   │  Cursor, …)      │                       └────────────┬─────────────┘  │
│   └──────────────────┘                                    │                │
│                                                           ▼                │
│   ┌──────────────────┐    --task=X args      ┌──────────────────────────┐  │
│   │ Cloud Scheduler  │──────────────────────▶│ Cloud Run job            │  │
│   │ × 11 crons       │                       │ sre-runner (20 tasks)    │  │
│   └──────────────────┘                       └────────────┬─────────────┘  │
│                                                           │                │
│                                                           ▼                │
│   ┌──────────────────────────────────────────────────────────────────────┐ │
│   │  BigQuery — 8 tables + 1 view                                        │ │
│   │  workflows · events · gap_reports · approval_queue · incidents       │ │
│   │  pii_findings · cost_daily · cloud_monitoring_alerts                 │ │
│   │  pipeline_health_v1                                                  │ │
│   └──────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

## What it does

Sixteen audit bots, all sharing the same shape: pull state from some source, apply pure-logic rules, write findings to `gap_reports`, and let the rollup + RCA bots react.

| Bot | What it checks |
|---|---|
| [retry](docs/bots/retry.md) | Per-workflow retry with circuit breaker; triggers Cloud Run job re-executions |
| [anomaly](docs/bots/anomaly.md) | Isolation Forest outliers on configured numeric BigQuery columns |
| [job_uptime](docs/bots/job_uptime.md) | Missed runs, failing streaks, paused schedulers, dead registrations |
| [freshness](docs/bots/freshness.md) | Tables that aren't being updated on their declared cadence |
| [cost](docs/bots/cost.md) | Daily-spend z-score against rolling 28-day baseline |
| [cloud_monitoring](docs/bots/cloud_monitoring.md) | GCP alert policy sync (YAML → API) + active-incident pull |
| [data_catalog](docs/bots/data_catalog.md) | Structural completeness of the workflow registry |
| [ai_gov](docs/bots/ai_gov.md) | Policy-compliance audit (business_purpose, policy_refs) |
| [dependency](docs/bots/dependency.md) | pyproject.toml / requirements.txt risk: unpinned, wildcards, pre-release |
| [scm](docs/bots/scm.md) | Git hygiene: stale branches, unreviewed merges, long-open PRs |
| [test_coverage](docs/bots/test_coverage.md) | Cobertura XML parsed against per-module thresholds |
| [secret_iam](docs/bots/secret_iam.md) | Secret rotation age + project-level IAM hygiene |
| [security](docs/bots/security.md) | GCP Security Command Center findings forwarder |
| [pii](docs/bots/pii.md) | Cloud DLP inspection of configured BigQuery tables |
| [llm_safety](docs/bots/llm_safety.md) | Drift + adversarial prompt smoke checks against a configured LLM |
| [incidents](docs/bots/incidents.md) | Open / close lifecycle grouping gap_reports + events |
| [rca](docs/bots/rca.md) | LLM-generated root-cause narratives for exhausted retries |
| [rollup](docs/bots/rollup.md) | Weekly cross-bot summary email + promotion of high-severity findings |

See [docs/architecture.md](docs/architecture.md) for the full system design, [docs/bots/](docs/bots/) for per-bot rule documentation, and [docs/extending.md](docs/extending.md) to add your own bot.

## Quickstart — 10 minutes from clone to first findings

The full walkthrough lives in [docs/quickstart.md](docs/quickstart.md). The condensed version:

```bash
# 1. Clone
git clone https://github.com/aaronharris05/sre-free-mcp.git
cd sre-free-mcp

# 2. Enable APIs in your GCP project (one-time)
gcloud services enable \
  bigquery.googleapis.com run.googleapis.com cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com monitoring.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com artifactregistry.googleapis.com \
  --project=YOUR_PROJECT_ID

# 3. Build + push the container image
gcloud builds submit \
  --tag gcr.io/YOUR_PROJECT_ID/sre-free-mcp:latest \
  --project=YOUR_PROJECT_ID

# 4. Apply Terraform — creates service + job + 11 schedulers + BQ dataset + IAM
cd infra/terraform/examples/minimal
terraform init
terraform apply \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="container_image=gcr.io/YOUR_PROJECT_ID/sre-free-mcp:latest"

# 5. Install the BigQuery schema (one-time)
gcloud run jobs execute sre-runner \
  --region=us-central1 \
  --args=--task=install_ddl \
  --project=YOUR_PROJECT_ID

# 6. Register your first workflow + watch findings flow (see docs/quickstart.md)
```

## Architecture in one screen

- **Cloud Run service** (`sre-mcp-server`) — exposes nine MCP tools (`pipeline_health`, `recent_findings`, `list_workflows`, `register_workflow`, `lookup_workflow`, `run_task`, `list_tasks`, `open_incidents`, `pending_approvals`). HTTP/SSE transport; IAM-authenticated.
- **Cloud Run job** (`sre-runner`) — dispatches to any of 20 named tasks (`anomaly_sweep`, `retry_tick`, `freshness_sweep`, etc.). Each Cloud Scheduler cron triggers the job with one `--task=X` arg.
- **BigQuery** — eight base tables + one view in the `governance` dataset (configurable). Schema is bundled as SQL files; install via `sre-runner --task=install_ddl`.
- **Config** — five YAMLs in `config/`: install settings, retry-policy overrides, recipient groups, audit targets. Loaded with pydantic validation; missing files fall back to bundled examples.
- **Pluggable providers** — `EmailSender` (Null / SMTP) and `LLMProvider` (Null / Gemini) ABCs. Bring your own by subclassing.
- **Terraform module** — `infra/terraform/modules/sre/` creates everything in one apply.

See [docs/architecture.md](docs/architecture.md) for the full picture: data flow, extension model, security boundaries, IAM matrix.

## Configuration

Seven YAML files in `config/`:

| File | What it controls |
|---|---|
| `install.yaml` | Project ID, region, BigQuery dataset, SMTP, LLM provider |
| `retry_policies.yaml` | Per-workflow retry overrides (backoff curve, circuit breaker thresholds) |
| `recipients.yaml` | Team-group → email-list mapping for finding routing |
| `anomaly_targets.yaml` | BigQuery tables + metric columns to scan for outliers |
| `freshness_targets.yaml` | Tables with declared refresh-cadence SLAs |
| `alert_policies.yaml` | (Optional) Declarative GCP Cloud Monitoring alert policies |
| `pii_targets.yaml` | (Optional) Tables to inspect with Cloud DLP |

Each ships with a `*.example.yaml`. Customer copies `install.yaml` for their project; the others have safe defaults (empty target lists). Full reference: [docs/configuration.md](docs/configuration.md).

## Talking to the MCP server

Any MCP client can call the nine tools over SSE. Examples for Claude Desktop, Cursor, and the official `mcp` CLI: [docs/mcp-clients.md](docs/mcp-clients.md).

## Adding your own bot

The audit-bot pattern is locked in: `checks.py` (pure logic) + `audit.py` (I/O orchestration) + tests. About 200 lines of Python + a registration in `runner/tasks.py`. Walkthrough: [docs/extending.md](docs/extending.md).

## Project status

`v0.1.0` — first public release. 473 tests passing, 16 bots shipping, validated end-to-end against a real GCP project. API may shift before `v1.0`.

## Contributing

Bug reports, feature requests, and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE).
