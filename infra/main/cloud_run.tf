locals {
  common_environment = {
    MOFI_ENVIRONMENT             = "production"
    MOFI_LOG_LEVEL               = "INFO"
    MOFI_GOOGLE_CLOUD_PROJECT    = var.project_id
    MOFI_GOOGLE_CLOUD_LOCATION   = var.vertex_location
    MOFI_VERTEX_MODEL            = var.vertex_model
    MOFI_REQUEST_TIMEOUT_SECONDS = "20"
  }
}

resource "google_cloud_run_v2_service" "api" {
  project             = var.project_id
  name                = "${local.name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    labels = {
      database_config = substr(filesha256("${path.module}/templates/database-startup.sh.tftpl"), 0, 12)
    }

    service_account                  = google_service_account.api.email
    timeout                          = "30s"
    max_instance_request_concurrency = 8

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.main.name
        subnetwork = google_compute_subnetwork.serverless.name
        tags       = ["${local.name}-api"]
      }
    }

    containers {
      image = var.application_image

      ports {
        name           = "http1"
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.common_environment, {
          MOFI_DB_POOL_MIN_SIZE           = "1"
          MOFI_DB_POOL_MAX_SIZE           = "4"
          MOFI_DB_CONNECT_TIMEOUT_SECONDS = "10"
          MOFI_DB_STARTUP_TIMEOUT_SECONDS = "120"
          MOFI_MAX_AGENT_ROUNDS           = "4"
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "MOFI_DATABASE_DSN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database["api_dsn"].secret_id
            version = google_secret_manager_secret_version.api_dsn.version
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 5
        failure_threshold     = 30
        http_get {
          path = "/health"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 2
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.database,
    google_compute_subnetwork_iam_member.serverless_network_user,
    google_project_iam_member.api_vertex,
    google_secret_manager_secret_iam_member.api_dsn,
    google_secret_manager_secret_version.api_dsn,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_api" {
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "migrate" {
  project             = var.project_id
  name                = "${local.name}-migrate"
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.migrate.email
      max_retries     = 1
      timeout         = "300s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.name
          subnetwork = google_compute_subnetwork.serverless.name
          tags       = ["${local.name}-migrate"]
        }
      }

      containers {
        image   = local.migration_image
        command = ["python", "-m", "mofiagent", "migrate"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name = "MOFI_DATABASE_DSN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database["admin_dsn"].secret_id
              version = google_secret_manager_secret_version.admin_dsn.version
            }
          }
        }
        env {
          name  = "MOFI_MIGRATIONS_DIR"
          value = "/app/migrations"
        }
        env {
          name  = "MOFI_DB_CONNECT_TIMEOUT_SECONDS"
          value = "10"
        }
        env {
          name  = "MOFI_DB_STARTUP_TIMEOUT_SECONDS"
          value = "120"
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.database,
    google_compute_subnetwork_iam_member.serverless_network_user,
    google_secret_manager_secret_iam_member.admin_dsn,
    google_secret_manager_secret_version.admin_dsn,
  ]
}

resource "google_cloud_run_v2_job" "ingest" {
  project             = var.project_id
  name                = "${local.name}-ingest"
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.ingest.email
      max_retries     = 2
      timeout         = "300s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.name
          subnetwork = google_compute_subnetwork.serverless.name
          tags       = ["${local.name}-ingest"]
        }
      }

      containers {
        image   = var.application_image
        command = ["python", "-m", "mofiagent", "ingest"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        dynamic "env" {
          for_each = {
            MOFI_ENVIRONMENT                = "production"
            MOFI_LOG_LEVEL                  = "INFO"
            MOFI_DB_POOL_MIN_SIZE           = "1"
            MOFI_DB_POOL_MAX_SIZE           = "2"
            MOFI_DB_CONNECT_TIMEOUT_SECONDS = "10"
            MOFI_DB_STARTUP_TIMEOUT_SECONDS = "120"
            MOFI_REQUEST_TIMEOUT_SECONDS    = "20"
          }
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "MOFI_DATABASE_DSN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database["ingest_dsn"].secret_id
              version = google_secret_manager_secret_version.ingest_dsn.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_compute_instance.database,
    google_compute_subnetwork_iam_member.serverless_network_user,
    google_secret_manager_secret_iam_member.ingest_dsn,
    google_secret_manager_secret_version.ingest_dsn,
  ]
}
