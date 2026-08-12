resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "mofi-api"
  display_name = "MoFiAgent Cloud Run API"
}

resource "google_service_account" "ingest" {
  project      = var.project_id
  account_id   = "mofi-ingest"
  display_name = "MoFiAgent Treasury ingestion"
}

resource "google_service_account" "migrate" {
  project      = var.project_id
  account_id   = "mofi-migrate"
  display_name = "MoFiAgent schema migration"
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "mofi-scheduler"
  display_name = "MoFiAgent Cloud Scheduler invoker"
}

resource "google_service_account" "database" {
  project      = var.project_id
  account_id   = "mofi-database"
  display_name = "MoFiAgent TimescaleDB VM"
}

resource "google_project_iam_member" "api_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "database_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.database.email}"
}

resource "google_project_iam_member" "database_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.database.email}"
}

resource "google_compute_subnetwork_iam_member" "serverless_network_user" {
  project    = var.project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.serverless.name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"
}
