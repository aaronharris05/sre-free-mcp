# sre-free-mcp Terraform module

Drops sre-free-mcp into one GCP project. After `terraform apply` and a
single bootstrap command, you have:

- a BigQuery dataset holding the governance tables
- a Cloud Run service running the MCP server (HTTP/SSE)
- a Cloud Run job running `sre-runner --task=<name>`
- Cloud Scheduler jobs hitting that runner on the cron you configured
- a service account with the IAM roles every audit bot needs

## Usage

```hcl
module "sre" {
  source = "github.com/aaronharris05/sre-free-mcp//infra/terraform/modules/sre?ref=main"

  project_id      = "my-gcp-project"
  region          = "us-central1"
  container_image = "gcr.io/my-gcp-project/sre-free-mcp:latest"

  # Optional — mount config from Secret Manager
  config_secret_name   = "sre-config"
  smtp_password_secret = "sre-smtp-password"
  llm_api_key_secret   = "sre-llm-api-key"

  # Optional — override the default schedule
  enabled_tasks = {
    retry_tick             = "*/5 * * * *"
    anomaly_sweep          = "0 4 * * *"
    freshness_sweep        = "30 4 * * *"
    cost_sweep             = "0 5 * * *"
    job_uptime_sweep       = "0 6 * * *"
    cloud_monitoring_sweep = "*/15 * * * *"
    data_catalog_audit     = "0 7 * * 1"
    ai_gov_audit           = "0 7 * * 1"
    incidents_tick         = "*/15 * * * *"
    rca_tick               = "*/30 * * * *"
    rollup                 = "0 13 * * 1"
  }
}
```

## After `terraform apply`

1. Apply the schema (one-time bootstrap — uses the bundled DDL files):

   ```bash
   gcloud run jobs execute sre-runner \
     --region=us-central1 \
     --args=--task=install_ddl \
     --project=my-gcp-project
   ```

   The module's `bootstrap_command` output prints the exact command for
   your install.

2. Register your first workflow via the MCP server, or directly:

   ```sql
   INSERT INTO `my-gcp-project.governance.workflows` (
     name, cron, trigger_kind, idempotent, owner_team, business_purpose,
     source_path, status, created_at, updated_at
   ) VALUES (
     'my_first_workflow', '0 4 * * *', 'scheduler', true, 'data_owners',
     'Daily customer rollup', 'agents/customer_rollup.py', 'active',
     CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
   );
   ```

3. The Cloud Scheduler jobs start firing on their cron; audit findings
   flow into `governance.gap_reports`; query via the MCP server.

## What gets created

| Resource | Default name | Notes |
|---|---|---|
| `google_bigquery_dataset` | `governance` | rename via `governance_dataset` |
| `google_service_account` | `sre-free-mcp` | rename via `service_account_id` |
| `google_cloud_run_v2_service` | `sre-mcp-server` | MCP HTTP/SSE on :8080 |
| `google_cloud_run_v2_job` | `sre-runner` | jobs override args via scheduler |
| `google_cloud_scheduler_job` × N | `sre-runner-{task}` | one per entry in `enabled_tasks` |
| Project IAM bindings | — | bigquery, run, scheduler, monitoring, logging |

## What it does NOT create

- **The container image** — build it yourself with `docker build` /
  `gcloud builds submit` and pass via `container_image`.
- **Secret values** — the module references `smtp_password_secret` and
  `llm_api_key_secret` by name and grants the SA accessor role on each,
  but you populate the secret values out-of-band.
- **The config files** — by default the container falls back to the
  bundled example configs. For production, build a tarball / archive
  of your `config/` and store it in Secret Manager, then pass
  `config_secret_name` (one-file mode supported for `install.yaml`;
  multi-file mode is on the roadmap).

## Inputs

See [`variables.tf`](variables.tf) for the full list.

## Outputs

| Output | Use |
|---|---|
| `service_url` | hand to your MCP client (Claude Desktop, Cursor, …) |
| `service_account_email` | grant cross-project IAM if needed |
| `job_name` | for manual `gcloud run jobs execute` calls |
| `dataset_id` | confirms the governance dataset name |
| `scheduler_jobs` | map of task → scheduler job for debugging |
| `bootstrap_command` | exact `gcloud` command to run the schema installer |
