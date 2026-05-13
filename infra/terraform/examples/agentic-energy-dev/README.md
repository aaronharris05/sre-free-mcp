# Minimal sre-free-mcp install

The smallest working example. One project, default schedule, image
already built and pushed to a registry.

## Prerequisites

1. A GCP project with billing enabled.
2. APIs enabled: `bigquery.googleapis.com`, `run.googleapis.com`,
   `cloudscheduler.googleapis.com`, `monitoring.googleapis.com`,
   `secretmanager.googleapis.com`, `iam.googleapis.com`.

   ```bash
   gcloud services enable \
     bigquery.googleapis.com \
     run.googleapis.com \
     cloudscheduler.googleapis.com \
     monitoring.googleapis.com \
     secretmanager.googleapis.com \
     iam.googleapis.com \
     --project=YOUR_PROJECT_ID
   ```

3. A container image built from this repo and pushed somewhere your
   project can pull from:

   ```bash
   cd <repo root>
   gcloud builds submit \
     --tag gcr.io/YOUR_PROJECT_ID/sre-free-mcp:latest \
     --project=YOUR_PROJECT_ID \
     -f docker/Dockerfile \
     .
   ```

## Apply

```bash
cd infra/terraform/examples/minimal
terraform init
terraform apply \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="container_image=gcr.io/YOUR_PROJECT_ID/sre-free-mcp:latest"
```

## Bootstrap

After the apply finishes, run the schema installer once:

```bash
$(terraform output -raw bootstrap_command)
```

The MCP server is now reachable at `terraform output service_url`.
Schedulers start firing on their cron; findings will start flowing
into `governance.gap_reports` as workflows get registered.
