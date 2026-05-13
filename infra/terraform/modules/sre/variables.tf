# sre-free-mcp Terraform module — input variables.

variable "project_id" {
  description = "GCP project ID this install lives in."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run + Cloud Scheduler."
  type        = string
  default     = "us-central1"
}

variable "container_image" {
  description = "Fully-qualified container image (e.g. gcr.io/PROJECT/sre-free-mcp:latest)."
  type        = string
}

variable "governance_dataset" {
  description = "BigQuery dataset that holds the governance tables. Default 'governance'."
  type        = string
  default     = "governance"
}

variable "service_name" {
  description = "Name of the Cloud Run MCP server service."
  type        = string
  default     = "sre-mcp-server"
}

variable "job_name" {
  description = "Name of the Cloud Run runner job."
  type        = string
  default     = "sre-runner"
}

variable "service_account_id" {
  description = "Short service-account ID (no domain). Will be created if it does not exist."
  type        = string
  default     = "sre-free-mcp"
}

variable "config_secret_name" {
  description = <<-EOT
    Name of the Secret Manager secret containing the tarball / archive of
    the config/ directory. Mounted as a volume at /app/config in both
    the service and the job. Leave empty to ship with built-in example
    configs (NOT recommended for production).
  EOT
  type        = string
  default     = ""
}

variable "smtp_password_secret" {
  description = "Secret Manager secret name (NOT value) holding SMTP password."
  type        = string
  default     = ""
}

variable "llm_api_key_secret" {
  description = "Secret Manager secret name (NOT value) holding the LLM API key."
  type        = string
  default     = ""
}

variable "enabled_tasks" {
  description = <<-EOT
    Map from task name to cron schedule. Each entry creates a Cloud
    Scheduler job that triggers sre-runner with --task=<name>. Set the
    value to an empty string to disable that task.

    Example:
      {
        retry_tick             = "*/5 * * * *"
        anomaly_sweep          = "0 4 * * *"
        freshness_sweep        = "30 4 * * *"
        cost_sweep             = "0 5 * * *"
        job_uptime_sweep       = "0 6 * * *"
        cloud_monitoring_sweep = "*/15 * * * *"
        data_catalog_audit     = "0 7 * * 1"   # weekly Monday
        ai_gov_audit           = "0 7 * * 1"
        incidents_tick         = "*/15 * * * *"
        rca_tick               = "*/30 * * * *"
        rollup                 = "0 13 * * 1"   # weekly Monday 13:00
      }
  EOT
  type        = map(string)
  default = {
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
    # Slices 13-16. Disabled by default ("") — customer enables by
    # populating the supporting config (PII targets, SCC org_id,
    # GitHub token secret, dependency / coverage env vars).
    dependency_audit    = ""
    scm_audit           = ""
    test_coverage_audit = ""
    secret_iam_audit    = "0 7 * * *"
    security_audit      = ""
    pii_audit           = ""
    llm_safety_audit    = ""
  }
}

variable "service_cpu" {
  description = "CPU allocation for the MCP server service."
  type        = string
  default     = "1"
}

variable "service_memory" {
  description = "Memory allocation for the MCP server service."
  type        = string
  default     = "512Mi"
}

variable "job_cpu" {
  description = "CPU allocation for the runner job."
  type        = string
  default     = "1"
}

variable "job_memory" {
  description = "Memory allocation for the runner job."
  type        = string
  default     = "1Gi"
}

variable "job_timeout_seconds" {
  description = "Per-execution timeout for the runner job."
  type        = number
  default     = 1800
}

variable "service_invokers" {
  description = <<-EOT
    Identities that can call the MCP server (run.invoker role). Use
    'allUsers' for a public read-only deployment, or restrict to
    specific service accounts / users.
  EOT
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels applied to every resource the module creates."
  type        = map(string)
  default = {
    managed_by = "sre-free-mcp"
  }
}
