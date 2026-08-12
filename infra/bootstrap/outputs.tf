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
