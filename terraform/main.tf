# Definicja dostawcy (kto zarządza zasobami)
terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0.1"
    }
  }
}

provider "docker" {}

# ... (początek z terraform i provider docker zostaje bez zmian)

resource "docker_network" "devops_net" {
  name = "devops_network"
}

resource "docker_volume" "db_data" {
  name = "devops_db_data"
}

# 1. Definicja BAZY DANYCH (tego Ci brakuje!)
resource "docker_image" "postgres_image" {
  name         = "postgres:15-alpine"
  keep_locally = false
}

resource "docker_container" "db_container" {
  image = docker_image.postgres_image.image_id
  name  = "devops_database"
  env = [
    "POSTGRES_PASSWORD=${var.db_password}",
    "POSTGRES_USER=admin",
    "POSTGRES_DB=form_data"
  ]
  ports {
    internal = 5432
    external = var.external_port
  }
  networks_advanced {
    name = docker_network.devops_net.name
  }

  volumes {
    volume_name    = docker_volume.db_data.name
    container_path = "/var/lib/postgresql/data"
  }
}

# 2. Definicja TWOJEJ APLIKACJI (to już masz w kodzie)
resource "docker_image" "app_image" {
  name = "moja-aplikacja-backend:latest"
  build {
    context = "../app/backend" # Sprawdź czy ścieżka do folderu z Dockerfile jest poprawna
  }
}

resource "docker_container" "app_container" {
  image = docker_image.app_image.name
  name  = "backend_service"
  
  env = [
    "DB_HOST=devops_database",
    "DB_PORT=5432",
    "DB_PASS=${var.db_password}"
  ]
  
  ports {
    internal = 5000
    external = 8080
  }

  depends_on = [docker_container.db_container]
  
  networks_advanced {
    name = docker_network.devops_net.name
  }
}

# --- MONITORING: PROMETHEUS ---
resource "docker_image" "prometheus_image" {
  name         = "prom/prometheus:latest"
  keep_locally = false
}

resource "docker_container" "prometheus_container" {
  name  = "devops_prometheus"
  image = docker_image.prometheus_image.image_id
  
  ports {
    internal = 9090
    external = 9090
  }
  
  networks_advanced {
    name = docker_network.devops_net.name
  }

  # Montujemy nasz plik konfiguracyjny
  volumes {
    host_path      = abspath("${path.module}/../monitoring/prometheus.yml")
    container_path = "/etc/prometheus/prometheus.yml"
  }
}

# --- MONITORING: GRAFANA ---
resource "docker_image" "grafana_image" {
  name         = "grafana/grafana:latest"
  keep_locally = false
}

resource "docker_container" "grafana_container" {
  name  = "devops_grafana"
  image = docker_image.grafana_image.image_id
  
  ports {
    internal = 3000
    external = 3000
  }
  
  networks_advanced {
    name = docker_network.devops_net.name
  }
}