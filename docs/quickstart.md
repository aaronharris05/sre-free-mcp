# Quickstart

Goal: clone the repo, deploy into a GCP project, and see real findings in BigQuery — in under 10 minutes.

## Prerequisites

- A GCP project with billing enabled. You'll spend ~$0.05 on Cloud Build for the image build, and ~$1.10/month thereafter for 11 Cloud Scheduler jobs. (You can `terraform destroy` afterwards to drop the recurring cost to $0.)
- `gcloud` CLI installed and authenticated as a user with `roles/editor` (or equivalent) on the project.
- `terraform` ≥ 1.6.
- `git`.

That's it — no Python install needed if you only want to deploy. (If you want to run the test suite or develop locally, see [CONTRIBUTING.md](../CONTRIBUTING.md).)

## Step 1 — Clone

```bash
git clone https://github.com/aaronharris05/sre-free-mcp.git
cd sre-free-mcp
```

## Step 2 — Enable APIs in your GCP project (one-time)

```bash
export PROJECT_ID=your-gcp-project

gcloud services enable \
  bigquery.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  monitoring.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  artifactregistry.googleapis.com \
  --project=$PROJECT_ID
```

Takes about a minute. Free.

## Step 3 — Build and push the container image

```bash
gcloud builds submit \
  --tag gcr.io/$PROJECT_ID/sre-free-mcp:latest \
  --project=$PROJECT_ID
```

This uses the `cloudbuild.yaml` at the repo root, which points Cloud Build at `docker/Dockerfile`. Expect ~2 minutes and ~$0.02.

## Step 4 — Apply Terraform

```bash
cd infra/terraform/examples/minimal
terraform init
terraform apply \
  -var="project_id=$PROJECT_ID" \
  -var="container_image=gcr.io/$PROJECT_ID/sre-free-mcp:latest"
```

This creates ~25 resources:

- 1 BigQuery dataset (`governance`)
- 1 service account (`sre-free-mcp@$PROJECT_ID.iam.gserviceaccount.com`) + project-level IAM bindings
- 1 Cloud Run service (`sre-mcp-server`) — the MCP server
- 1 Cloud Run job (`sre-runner`) — the audit dispatcher
- 11 Cloud Scheduler jobs (one per default-enabled task)

Expect ~2 minutes. Look for the printed outputs at the end:

```
service_url        = "https://sre-mcp-server-XXXXXXXX-uc.a.run.app"
bootstrap_command  = "gcloud run jobs execute sre-runner --region=us-central1 --args=--task=install_ddl --project=YOUR_PROJECT_ID"
```

### If you already have a `governance` BigQuery dataset

The default `governance_dataset` name is `governance`. If your project already has one from other tooling, the apply will fail with "dataset already exists." Override with:

```bash
terraform apply ... -var="governance_dataset=sre_governance"
```

Any non-conflicting name works.

## Step 5 — Install the BigQuery schema (one-time bootstrap)

Run the command Terraform printed:

```bash
$(terraform output -raw bootstrap_command)
```

This invokes the `install_ddl` task once. It applies eight `CREATE TABLE IF NOT EXISTS` and one `CREATE OR REPLACE VIEW`. Idempotent — re-running is safe and equivalent to a no-op.

Verify:

```bash
bq ls $PROJECT_ID:governance
```

Should list `workflows`, `events`, `gap_reports`, `approval_queue`, `incidents`, `pii_findings`, `cost_daily`, `cloud_monitoring_alerts`, `pipeline_health_v1`.

## Step 6 — Register a workflow and produce real findings

Register one workflow so the audits have something to evaluate:

```bash
bq query --use_legacy_sql=false "
INSERT INTO \`$PROJECT_ID.governance.workflows\` (
  name, cron, trigger_kind, idempotent, owner_team, business_purpose,
  source_path, status, created_at, updated_at, source
) VALUES (
  'my_first_workflow',
  '0 4 * * *',
  'scheduler',
  TRUE,
  'governance_owners',
  'Daily customer rollup against authentication logs.',
  'agents/customer_rollup.py',
  'active',
  CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(),
  'manual'
)"
```

Trigger an audit:

```bash
gcloud run jobs execute sre-runner \
  --region=us-central1 \
  --args=--task=data_catalog_audit \
  --project=$PROJECT_ID \
  --wait
```

The audit checks structural completeness of your workflow row. A complete row produces zero findings.

To see the audit actually catch something, register a broken workflow:

```bash
bq query --use_legacy_sql=false "
INSERT INTO \`$PROJECT_ID.governance.workflows\` (
  name, cron, trigger_kind, idempotent, owner_team, business_purpose,
  source_path, status, created_at, updated_at, source
) VALUES (
  'broken_workflow',
  '0 4 * * *',
  'scheduler',
  NULL, NULL, NULL, NULL,
  'active',
  CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(),
  'manual'
)"
```

Re-run the audit. The broken row produces three findings: `missing_owner_team` (high), `missing_idempotent_flag` (medium), `missing_source_path` (medium).

```bash
bq query --use_legacy_sql=false "
SELECT scope, scope_id, severity, gap_kind
FROM \`$PROJECT_ID.governance.gap_reports\`
WHERE resolved_at IS NULL
ORDER BY severity DESC, generated_at DESC
"
```

If you see the findings — your install works end-to-end.

## Step 7 — Hook up an MCP client (optional)

The Cloud Run service is reachable at the URL Terraform printed (`https://sre-mcp-server-XXXXX-uc.a.run.app/sse`). Authentication uses your gcloud identity token:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" "$(terraform output -raw service_url)/sse"
```

You should see SSE chatter (the connection holds open).

For Claude Desktop, Cursor, or other MCP clients: see [mcp-clients.md](mcp-clients.md).

## Step 8 — Configure real audit targets (optional)

The bundled `config/*.example.yaml` ship with placeholder targets. To run anomaly detection on a real table or freshness audits on real datasets, override the configs:

1. Edit `config/anomaly_targets.yaml`, `config/freshness_targets.yaml`, etc.
2. Either rebuild the image (`gcloud builds submit ...`) or mount via Secret Manager — see [configuration.md](configuration.md#config-overrides-in-production).

## Step 9 — Watch the schedulers

The 11 default schedulers start firing immediately on their cron schedule:

- `retry_tick` every 5 min
- `cloud_monitoring_sweep` every 15 min
- `incidents_tick` every 15 min
- `rca_tick` every 30 min
- `anomaly_sweep`, `freshness_sweep`, `cost_sweep`, `job_uptime_sweep` daily 4-6am UTC
- `data_catalog_audit`, `ai_gov_audit`, `rollup` weekly Monday

Within an hour you should see structured-JSON log lines from the early ticks in Cloud Logging. Check:

```bash
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=sre-runner' \
  --project=$PROJECT_ID \
  --limit=10 \
  --format='value(jsonPayload.message)'
```

## Step 10 — Cleanup (when you're done evaluating)

```bash
cd infra/terraform/examples/minimal
terraform destroy \
  -var="project_id=$PROJECT_ID" \
  -var="container_image=gcr.io/$PROJECT_ID/sre-free-mcp:latest"
```

Destroys everything Terraform created. Container image and any findings that landed in BigQuery during testing remain — drop those manually with `bq rm -r -f $PROJECT_ID:governance` and `gcloud container images delete ...` if you want a pristine project.

## Common issues

- **`terraform apply` fails on dataset creation** — you already have a `governance` dataset. Use `-var="governance_dataset=sre_governance"`.
- **Cloud Run service deployment hangs / fails health check** — usually a missing API. Re-run the `gcloud services enable` block.
- **`install_ddl` job fails on the view** — typically means it's targeting the wrong dataset and finding a legacy `workflows` table without the expected columns. Confirm `SRE_GOVERNANCE_DATASET` env var on the job matches your `var.governance_dataset`. Terraform sets this for you, but if you customized, check.
- **MCP client can't connect** — check `service_invokers` includes your identity. By default the service is private to its own SA. Add yourself via `-var='service_invokers=["user:you@example.com"]'`.

## Next steps

- [bots/](bots/) — read up on each audit and what it catches
- [configuration.md](configuration.md) — wire real anomaly / freshness / cost targets
- [extending.md](extending.md) — write your own bot
- [mcp-clients.md](mcp-clients.md) — connect Claude Desktop / Cursor
