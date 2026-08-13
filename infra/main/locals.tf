locals {
  name = "mofiagent"
  labels = {
    application = "mofiagent"
    environment = "takehome"
    managed_by  = "terraform"
  }

  database_name = "mofiagent"
  database_port = 5432
  database_users = {
    admin  = "mofiadmin"
    api    = "mofi_api"
    ingest = "mofi_ingest"
  }

  serverless_subnet_cidr = "10.20.0.0/26"
  database_subnet_cidr   = "10.20.1.0/28"
  migration_image        = coalesce(var.migration_image, var.application_image)

  required_apis = toset([
    "aiplatform.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
}
