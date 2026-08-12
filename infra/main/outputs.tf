output "api_url" {
  description = "Public MoFiAgent HTTPS endpoint."
  value       = google_cloud_run_v2_service.api.uri
}

output "region" {
  description = "Primary regional resource location."
  value       = var.region
}

output "database_vm_name" {
  description = "Private TimescaleDB Compute Engine VM name."
  value       = google_compute_instance.database.name
}

output "database_private_ip" {
  description = "Private database IP for operational troubleshooting."
  value       = google_compute_address.database.address
}

output "migration_job_name" {
  description = "Run this job after every schema change and before ingestion."
  value       = google_cloud_run_v2_job.migrate.name
}

output "ingestion_job_name" {
  description = "Cloud Run Job used for scheduled and manual Treasury ingestion."
  value       = google_cloud_run_v2_job.ingest.name
}
