# sre-free-mcp

> Self-hosted SRE agents for GCP. Anomaly detection, retry orchestration, and circuit breaking — exposed as an MCP server so your AI tools can drive them.

**Status:** v0.1 — under active development. Not yet ready for production.

## What this is

`sre-free-mcp` installs a small but opinionated SRE toolkit into your own Google Cloud project. After one `terraform apply` you get:

- **An anomaly detection bot** that scans configured BigQuery tables on a schedule, scores each row with a leaderboard of detectors (Isolation Forest / LOF / OCSVM / ECOD / PCA), writes findings to a governance table, and emails the right owner.
- **A retry orchestrator** that watches a pipeline-health view, applies a per-workflow retry policy with circuit breaking, and reruns failed Cloud Run jobs — vetoing anything marked non-idempotent.
- **An MCP server** that exposes every capability above as MCP tools (`scan_anomalies`, `retry_workflow`, `circuit_breaker_state`, …) so an LLM agent can drive them on demand.

All of it runs in your project, on your data, using your service accounts. Nothing leaves your perimeter.

## Why this exists

Most SRE tooling is either (a) a SaaS that wants your data, or (b) a heavyweight platform like Prometheus + Alertmanager + custom-everything. This is the small middle option for teams running mostly cron jobs and ingest pipelines on GCP: a few hundred lines of Python wired into BigQuery and Cloud Run, configured through three YAML files, and deployable in an afternoon.

## Architecture

```
Your GCP Project
├── Cloud Run SERVICE: sre-mcp-server      ← MCP over HTTP/SSE
├── Cloud Run JOB:     sre-runner          ← --task=retry_tick | --task=anomaly_sweep
├── Cloud Scheduler:   every 5 min         → runner --task=retry_tick
├── Cloud Scheduler:   daily 04:00 UTC     → runner --task=anomaly_sweep
├── BigQuery dataset:  governance          (gap_reports, events, approval_queue, workflows, pipeline_health_v1)
├── Secret Manager:    smtp_password, llm_api_key
└── Service accounts + IAM
```

See [`docs/architecture.md`](docs/architecture.md) for the deeper version.

## Installation

```bash
# 1. Clone
git clone https://github.com/aaronharris05/sre-free-mcp.git
cd sre-free-mcp

# 2. Copy and edit the example configs
cp config/anomaly_targets.example.yaml config/anomaly_targets.yaml
cp config/retry_policies.example.yaml   config/retry_policies.yaml
cp config/recipients.example.yaml       config/recipients.yaml
cp config/install.example.yaml          config/install.yaml

# 3. Build and push the container image
gcloud builds submit --tag gcr.io/$PROJECT_ID/sre-free-mcp:latest

# 4. Apply the Terraform module
cd infra/terraform/examples/minimal
terraform init
terraform apply
```

Full walkthrough in [`docs/install.md`](docs/install.md).

## Configuration

Three YAML files drive the bot's behavior:

- **`anomaly_targets.yaml`** — which BQ tables to scan, what columns, what thresholds.
- **`retry_policies.yaml`** — per-workflow retry counts, backoff curves, circuit-breaker windows.
- **`recipients.yaml`** — who gets emailed when a finding fires.

See the `.example.yaml` files in [`config/`](config/) for the full schema.

## License

[Apache 2.0](LICENSE). Contributions welcome — see [`docs/contributing.md`](docs/contributing.md).
