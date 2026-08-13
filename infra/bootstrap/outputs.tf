output "state_bucket_name" {
  description = "GCS bucket used by the main stack backend."
  value       = google_storage_bucket.terraform_state.name
}

output "artifact_repository" {
  description = "Artifact Registry repository resource name."
  value       = google_artifact_registry_repository.application.name
}

output "docker_repository" {
  description = "Docker push prefix for application images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.application.repository_id}"
}

output "build_service_account" {
  description = "Least-privilege service account used by Cloud Build."
  value       = google_service_account.build.email
}

output "build_source_bucket" {
  description = "GCS bucket used to stage Cloud Build source archives."
  value       = google_storage_bucket.build_source.name
}
