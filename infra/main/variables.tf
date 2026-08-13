variable "project_id" {
  description = "Existing Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Primary region for compute, data, and Cloud Run."
  type        = string
  default     = "us-east1"
}

variable "zone" {
  description = "Compute Engine zone for the single database VM."
  type        = string
  default     = "us-east1-b"
}

variable "vertex_location" {
  description = "Gemini inference endpoint; current GA PayGo uses the US multi-region."
  type        = string
  default     = "us"
}

variable "vertex_model" {
  description = "GA Gemini model ID used for bounded function calling."
  type        = string
  default     = "gemini-3.5-flash"
}

variable "application_image" {
  description = "Immutable Artifact Registry application image reference."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.application_image))
    error_message = "application_image must be pinned to an immutable sha256 digest."
  }
}

variable "migration_image" {
  description = "Optional image override used to run additive migrations before the application rollout."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.migration_image == null ||
      can(regex("^[a-z0-9.-]+-docker\\.pkg\\.dev/.+@sha256:[0-9a-f]{64}$", var.migration_image))
    )
    error_message = "migration_image must be null or an Artifact Registry image pinned by sha256 digest."
  }
}

variable "timescaledb_image" {
  description = "Verified immutable TimescaleDB image reference."
  type        = string
  default     = "timescale/timescaledb@sha256:3e65c273cbf7218f2a28692492b4625b49f35c7d2142e945561ef52311a8c83a"

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.timescaledb_image))
    error_message = "timescaledb_image must be pinned to an immutable sha256 digest."
  }
}

variable "db_machine_type" {
  description = "Compute Engine machine type for TimescaleDB."
  type        = string
  default     = "e2-small"
}

variable "db_disk_size_gb" {
  description = "Size of the separate TimescaleDB persistent disk."
  type        = number
  default     = 20

  validation {
    condition     = var.db_disk_size_gb >= 10
    error_message = "db_disk_size_gb must be at least 10 GB."
  }
}

variable "database_deletion_protection" {
  description = "Protect the database VM from deletion; set false only in a reviewed two-phase replacement workflow."
  type        = bool
  default     = true
}

variable "snapshot_retention_days" {
  description = "Maximum retention for daily database disk snapshots."
  type        = number
  default     = 30

  validation {
    condition     = var.snapshot_retention_days >= 7 && var.snapshot_retention_days <= 365
    error_message = "snapshot_retention_days must be between 7 and 365."
  }
}

variable "admin_secret_version_generation" {
  description = "Administrative credential generation; change only with the coordinated break-glass rotation procedure."
  type        = number
  default     = 1

  validation {
    condition     = var.admin_secret_version_generation >= 1
    error_message = "admin_secret_version_generation must be positive."
  }
}

variable "runtime_secret_version_generation" {
  description = "Increment intentionally to rotate the API and ingestion database credentials."
  type        = number
  default     = 1

  validation {
    condition     = var.runtime_secret_version_generation >= 1
    error_message = "runtime_secret_version_generation must be positive."
  }
}

variable "scheduler_cron" {
  description = "UTC cron expression for Treasury ingestion."
  type        = string
  default     = "0 2 * * *"
}
