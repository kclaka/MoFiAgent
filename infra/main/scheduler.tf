resource "google_cloud_run_v2_job_iam_member" "scheduler_ingest" {
  project  = var.project_id
  location = google_cloud_run_v2_job.ingest.location
  name     = google_cloud_run_v2_job.ingest.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "ingest" {
  project     = var.project_id
  name        = "${local.name}-daily-ingest"
  description = "Load official Treasury yields into TimescaleDB"
  region      = var.region
  schedule    = var.scheduler_cron
  time_zone   = "Etc/UTC"

  attempt_deadline = "320s"

  retry_config {
    retry_count          = 2
    max_retry_duration   = "900s"
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
    max_doublings        = 3
  }

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.ingest.name}:run"
    http_method = "POST"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_ingest]
}
