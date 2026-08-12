locals {
  secret_ids = {
    admin_password  = "${local.name}-db-admin-password"
    api_password    = "${local.name}-db-api-password"
    ingest_password = "${local.name}-db-ingest-password"
    admin_dsn       = "${local.name}-db-admin-dsn"
    api_dsn         = "${local.name}-db-api-dsn"
    ingest_dsn      = "${local.name}-db-ingest-dsn"
  }
}

resource "google_secret_manager_secret" "database" {
  for_each = local.secret_ids

  project             = var.project_id
  secret_id           = each.value
  deletion_protection = true

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.required]
}

ephemeral "random_password" "admin" {
  length      = 40
  special     = false
  min_lower   = 8
  min_numeric = 8
  min_upper   = 8
}

ephemeral "random_password" "api" {
  length      = 40
  special     = false
  min_lower   = 8
  min_numeric = 8
  min_upper   = 8
}

ephemeral "random_password" "ingest" {
  length      = 40
  special     = false
  min_lower   = 8
  min_numeric = 8
  min_upper   = 8
}

resource "google_secret_manager_secret_version" "admin_password" {
  secret                 = google_secret_manager_secret.database["admin_password"].id
  secret_data_wo         = ephemeral.random_password.admin.result
  secret_data_wo_version = var.admin_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_version" "api_password" {
  secret                 = google_secret_manager_secret.database["api_password"].id
  secret_data_wo         = ephemeral.random_password.api.result
  secret_data_wo_version = var.runtime_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_version" "ingest_password" {
  secret                 = google_secret_manager_secret.database["ingest_password"].id
  secret_data_wo         = ephemeral.random_password.ingest.result
  secret_data_wo_version = var.runtime_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_version" "admin_dsn" {
  secret = google_secret_manager_secret.database["admin_dsn"].id
  secret_data_wo = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=disable&connect_timeout=5",
    local.database_users.admin,
    ephemeral.random_password.admin.result,
    google_compute_address.database.address,
    local.database_port,
    local.database_name,
  )
  secret_data_wo_version = var.admin_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_version" "api_dsn" {
  secret = google_secret_manager_secret.database["api_dsn"].id
  secret_data_wo = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=disable&connect_timeout=5",
    local.database_users.api,
    ephemeral.random_password.api.result,
    google_compute_address.database.address,
    local.database_port,
    local.database_name,
  )
  secret_data_wo_version = var.runtime_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_version" "ingest_dsn" {
  secret = google_secret_manager_secret.database["ingest_dsn"].id
  secret_data_wo = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=disable&connect_timeout=5",
    local.database_users.ingest,
    ephemeral.random_password.ingest.result,
    google_compute_address.database.address,
    local.database_port,
    local.database_name,
  )
  secret_data_wo_version = var.runtime_secret_version_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret_iam_member" "api_dsn" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database["api_dsn"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "ingest_dsn" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database["ingest_dsn"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_secret_manager_secret_iam_member" "admin_dsn" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database["admin_dsn"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.migrate.email}"
}

resource "google_secret_manager_secret_iam_member" "database_passwords" {
  for_each = toset(["admin_password", "api_password", "ingest_password"])

  project   = var.project_id
  secret_id = google_secret_manager_secret.database[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.database.email}"
}
