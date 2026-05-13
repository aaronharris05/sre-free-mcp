# Minimal sre-free-mcp install.
#
# The smallest working example: one project, default schedule, no
# external secrets, image already built and pushed somewhere.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0, < 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "sre" {
  source = "../../modules/sre"

  project_id         = var.project_id
  region             = var.region
  container_image    = var.container_image
  governance_dataset = var.governance_dataset
}

variable "project_id" {
  type        = string
  description = "GCP project ID."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "GCP region."
}

variable "container_image" {
  type        = string
  description = "Container image — e.g. gcr.io/PROJECT/sre-free-mcp:latest"
}

variable "governance_dataset" {
  type        = string
  default     = "governance"
  description = <<-EOT
    BigQuery dataset name for sre-free-mcp's tables. Override when
    installing alongside an existing `governance` dataset from another
    tool (e.g. set to "sre_governance" to keep the new tables separate
    from legacy ones).
  EOT
}

output "service_url" {
  value = module.sre.service_url
}

output "bootstrap_command" {
  value = module.sre.bootstrap_command
}
