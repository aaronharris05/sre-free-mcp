# sre-free-mcp Terraform module — outputs.

output "service_url" {
  description = "HTTPS URL of the deployed MCP server service."
  value       = google_cloud_run_v2_service.mcp.uri
}

output "service_account_email" {
  description = "Service account every sre-free-mcp resource runs as."
  value       = google_service_account.sa.email
}

output "job_name" {
  description = "Cloud Run job name for sre-runner."
  value       = google_cloud_run_v2_job.runner.name
}

output "dataset_id" {
  description = "BigQuery dataset that holds the governance tables."
  value       = google_bigquery_dataset.governance.dataset_id
}

output "scheduler_jobs" {
  description = "Map of scheduled task name → Cloud Scheduler job resource name."
  value       = { for k, j in google_cloud_scheduler_job.tasks : k => j.name }
}

output "bootstrap_command" {
  description = "Run this once after apply to install the BigQuery schema."
  value       = "gcloud run jobs execute ${google_cloud_run_v2_job.runner.name} --region=${var.region} --args=--task=install_ddl --project=${var.project_id}"
}
