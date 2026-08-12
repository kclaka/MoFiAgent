data "google_compute_image" "cos" {
  project = "cos-cloud"
  family  = "cos-stable"

  depends_on = [google_project_service.required]
}

resource "google_compute_address" "database" {
  project      = var.project_id
  name         = "${local.name}-database"
  region       = var.region
  address_type = "INTERNAL"
  address      = "10.20.1.2"
  subnetwork   = google_compute_subnetwork.database.id
}

resource "google_compute_disk" "database" {
  project                   = var.project_id
  name                      = "${local.name}-database-data"
  zone                      = var.zone
  type                      = "pd-balanced"
  size                      = var.db_disk_size_gb
  physical_block_size_bytes = 4096

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_resource_policy" "database_snapshots" {
  project = var.project_id
  name    = "${local.name}-database-daily"
  region  = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "04:00"
      }
    }

    retention_policy {
      max_retention_days    = var.snapshot_retention_days
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }

    snapshot_properties {
      guest_flush       = true
      labels            = local.labels
      storage_locations = ["us"]
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "database_snapshots" {
  project = var.project_id
  name    = google_compute_resource_policy.database_snapshots.name
  disk    = google_compute_disk.database.name
  zone    = var.zone
}

resource "google_compute_instance" "database" {
  project                   = var.project_id
  name                      = "${local.name}-database"
  zone                      = var.zone
  machine_type              = var.db_machine_type
  allow_stopping_for_update = true
  can_ip_forward            = false
  deletion_protection       = var.database_deletion_protection
  tags                      = ["${local.name}-database"]

  boot_disk {
    auto_delete = true
    initialize_params {
      image = data.google_compute_image.cos.self_link
      size  = 10
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.database.id
    device_name = "${local.name}-data"
    mode        = "READ_WRITE"
  }

  network_interface {
    network    = google_compute_network.main.id
    subnetwork = google_compute_subnetwork.database.id
    network_ip = google_compute_address.database.address
  }

  service_account {
    email  = google_service_account.database.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    block-project-ssh-keys = "true"
    enable-oslogin         = "true"
    serial-port-enable     = "false"
  }

  metadata_startup_script = templatefile("${path.module}/templates/database-startup.sh.tftpl", {
    project_id                = var.project_id
    timescaledb_image         = var.timescaledb_image
    data_device               = "/dev/disk/by-id/google-${local.name}-data"
    data_mount                = "/mnt/disks/${local.name}-data"
    database_name             = local.database_name
    database_port             = local.database_port
    serverless_subnet_cidr    = local.serverless_subnet_cidr
    admin_user                = local.database_users.admin
    api_user                  = local.database_users.api
    ingest_user               = local.database_users.ingest
    admin_password_secret_id  = google_secret_manager_secret.database["admin_password"].secret_id
    api_password_secret_id    = google_secret_manager_secret.database["api_password"].secret_id
    ingest_password_secret_id = google_secret_manager_secret.database["ingest_password"].secret_id
  })

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    provisioning_model  = "STANDARD"
    preemptible         = false
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = true
    enable_vtpm                 = true
  }

  depends_on = [
    google_compute_disk_resource_policy_attachment.database_snapshots,
    google_compute_router_nat.main,
    google_project_iam_member.database_logging,
    google_project_iam_member.database_monitoring,
    google_secret_manager_secret_iam_member.database_passwords,
    google_secret_manager_secret_version.admin_password,
    google_secret_manager_secret_version.api_password,
    google_secret_manager_secret_version.ingest_password,
  ]
}
