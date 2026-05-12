# sre-free-mcp Terraform module.
#
# Creates everything a customer needs to install sre-free-mcp into
# their own GCP project:
#
#   * BigQuery dataset for the governance tables
#   * Service account with the IAM roles every audit bot needs
#   * Cloud Run service running sre-mcp-server (MCP over HTTP/SSE)
#   * Cloud Run job running sre-runner --task=<name>
#   * Cloud Scheduler jobs (one per entry in var.enabled_tasks)
#   * Optional Secret Manager IAM bindings for SMTP + LLM credentials
#
# Schemas themselves are not created by Terraform — the customer runs
#   sre-runner --task=install_ddl
# once after `terraform apply` to apply the bundled CREATE TABLE IF
# NOT EXISTS statements. Keeps the SQL in one place (the Python module)
# instead of duplicated in HCL.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0, < 7.0"
    }
  }
}

# ---------------------------------------------------------------------------
# BigQuery dataset
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "governance" {
  project       = var.project_id
  dataset_id    = var.governance_dataset
  location      = var.region
  friendly_name = "sre-free-mcp governance"
  description   = "Tables backing sre-free-mcp: workflows, events, gap_reports, incidents, approval_queue, pii_findings, cost_daily, cloud_monitoring_alerts, plus the pipeline_health_v1 view."
  labels        = var.labels
}

# ---------------------------------------------------------------------------
# Service account + IAM
# ---------------------------------------------------------------------------

resource "google_service_account" "sa" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "sre-free-mcp service account"
  description  = "Runs the MCP server + runner job."
}

locals {
  sa_email = google_service_account.sa.email
  # Project-level roles the bots need. Scoped narrowly:
  #   bigquery.dataEditor  → write to governance.* tables
  #   bigquery.jobUser     → run queries
  #   run.invoker          → retry orchestrator triggers Cloud Run jobs
  #   run.viewer           → job_uptime audit lists jobs + executions
  #   cloudscheduler.viewer → job_uptime audit reads pause state
  #   monitoring.alertPolicyEditor → cloud_monitoring sync
  #   monitoring.viewer     → cloud_monitoring sweep reads alerts
  #   secretmanager.secretAccessor → SMTP + LLM secrets (granted per-secret below)
  project_roles = [
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/run.invoker",
    "roles/run.viewer",
    "roles/cloudscheduler.viewer",
    "roles/monitoring.alertPolicyEditor",
    "roles/monitoring.viewer",
    "roles/logging.logWriter",
  ]
}

resource "google_project_iam_member" "sa_roles" {
  for_each = toset(local.project_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${local.sa_email}"
}

# Optional per-secret accessor bindings — only created when the
# customer actually configured these secrets.
resource "google_secret_manager_secret_iam_member" "smtp_password_accessor" {
  count     = var.smtp_password_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = var.smtp_password_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.sa_email}"
}

resource "google_secret_manager_secret_iam_member" "llm_api_key_accessor" {
  count     = var.llm_api_key_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = var.llm_api_key_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.sa_email}"
}

# ---------------------------------------------------------------------------
# Cloud Run service — MCP server
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "mcp" {
  project  = var.project_id
  location = var.region
  name     = var.service_name
  labels   = var.labels

  # GCP provider 6.x defaults this to true; without it a `terraform
  # destroy` (and replace cycles, which the provider sometimes plans
  # when the image SHA changes) silently fails.
  deletion_protection = false

  template {
    service_account = local.sa_email

    scaling {
      max_instance_count = 5
      min_instance_count = 0
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = var.service_cpu
          memory = var.service_memory
        }
        cpu_idle = true
      }

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "SRE_CONFIG_DIR"
        value = "/app/config"
      }
      env {
        name  = "MCP_TRANSPORT"
        value = "sse"
      }

      dynamic "volume_mounts" {
        for_each = var.config_secret_name != "" ? [1] : []
        content {
          name       = "config"
          mount_path = "/app/config"
        }
      }

      ports {
        container_port = 8080
      }
    }

    dynamic "volumes" {
      for_each = var.config_secret_name != "" ? [1] : []
      content {
        name = "config"
        secret {
          secret = var.config_secret_name
          items {
            version = "latest"
            path    = "install.yaml"
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [google_project_iam_member.sa_roles]
}

# Public/restricted access — defaults to no invokers (private).
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.service_invokers)
  project  = var.project_id
  location = google_cloud_run_v2_service.mcp.location
  name     = google_cloud_run_v2_service.mcp.name
  role     = "roles/run.invoker"
  member   = each.key
}

# ---------------------------------------------------------------------------
# Cloud Run job — runner
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "runner" {
  project  = var.project_id
  location = var.region
  name     = var.job_name
  labels   = merge(var.labels, { component = "runner" })

  template {
    template {
      service_account = local.sa_email
      timeout         = "${var.job_timeout_seconds}s"

      containers {
        image = var.container_image
        # NOTE: Each Cloud Scheduler invocation overrides args via the
        # runJob request; this default just exists so the job can be
        # invoked manually. See google_cloud_scheduler_job.tasks below.
        command = ["sre-runner"]
        args    = ["--task=retry_tick"]

        resources {
          limits = {
            cpu    = var.job_cpu
            memory = var.job_memory
          }
        }

        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCP_REGION"
          value = var.region
        }
        env {
          name  = "SRE_CONFIG_DIR"
          value = "/app/config"
        }

        dynamic "volume_mounts" {
          for_each = var.config_secret_name != "" ? [1] : []
          content {
            name       = "config"
            mount_path = "/app/config"
          }
        }
      }

      dynamic "volumes" {
        for_each = var.config_secret_name != "" ? [1] : []
        content {
          name = "config"
          secret {
            secret = var.config_secret_name
            items {
              version = "latest"
              path    = "install.yaml"
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_iam_member.sa_roles]
}

# ---------------------------------------------------------------------------
# Cloud Scheduler — one entry per enabled task
# ---------------------------------------------------------------------------

locals {
  active_schedules = { for k, v in var.enabled_tasks : k => v if v != "" }
  job_resource     = "projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.runner.name}"
}

resource "google_cloud_scheduler_job" "tasks" {
  for_each = local.active_schedules

  project   = var.project_id
  region    = var.region
  name      = "${var.job_name}-${replace(each.key, "_", "-")}"
  schedule  = each.value
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.runner.name}:run"
    # Override the container args so this scheduler entry runs the right task.
    body = base64encode(jsonencode({
      overrides = {
        containerOverrides = [
          {
            args = ["--task=${each.key}"]
          }
        ]
      }
    }))

    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = local.sa_email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  retry_config {
    retry_count = 1
  }

  depends_on = [google_cloud_run_v2_job.runner]
}

# Scheduler service account needs to invoke the Cloud Run job.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_job.runner.location
  name     = google_cloud_run_v2_job.runner.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.sa_email}"
}
