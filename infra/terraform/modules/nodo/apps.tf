resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

locals {
  # The Azure environment (dev, staging, prod) differs from NODO_ENVIRONMENT: in Azure the API always
  # runs with NODO_ENVIRONMENT=prod so dev tokens, demo data and weak keys stay refused.
  common_env = {
    NODO_ENVIRONMENT                      = "prod"
    AZURE_CLIENT_ID                       = "" # set per app below, used by DefaultAzureCredential
    NODO_AZURE_STORAGE_ACCOUNT            = azurerm_storage_account.main.name
    NODO_AZURE_SERVICEBUS_FQDN            = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
    NODO_AZURE_SERVICEBUS_QUEUE           = azurerm_servicebus_queue.ingest.name
    NODO_AZURE_POSTGRES_HOST              = azurerm_postgresql_flexible_server.main.fqdn
    NODO_AZURE_POSTGRES_DATABASE          = azurerm_postgresql_flexible_server_database.nodo.name
    NODO_AZURE_SEARCH_ENDPOINT            = "https://${azurerm_search_service.main.name}.search.windows.net"
    NODO_AZURE_OPENAI_ENDPOINT            = azurerm_cognitive_account.openai.endpoint
    NODO_AZURE_SPEECH_ENDPOINT            = azurerm_cognitive_account.speech.endpoint
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
  }
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  ingress {
    external_enabled = true
    target_port      = var.api_target_port
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    http_scale_rule {
      name                = "http"
      concurrent_requests = "50"
    }

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = merge(local.common_env, {
          AZURE_CLIENT_ID    = azurerm_user_assigned_identity.api.client_id
          NODO_AUTH_MODE     = "oidc"
          NODO_OIDC_ISSUER   = var.oidc_issuer
          NODO_OIDC_AUDIENCE = var.oidc_audience
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "NODO_ATTESTATION_HMAC_KEY"
        secret_name = "attestation-hmac-key"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = var.api_target_port
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/readyz"
        port      = var.api_target_port
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    # The deploy pipeline rolls out new images; Terraform owns everything else.
    ignore_changes = [template[0].container[0].image]
  }
}

# Ingestion worker: starts when the queue has messages and scales to zero otherwise.
resource "azurerm_container_app_job" "worker" {
  name                         = "caj-${local.name}-worker"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 1
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.worker.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.worker.id
  }

  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = var.worker_max_executions
      polling_interval_in_seconds = 30

      rules {
        name             = "ingest-queue"
        custom_rule_type = "azure-servicebus"
        identity_id      = azurerm_user_assigned_identity.worker.id
        metadata = {
          queueName    = azurerm_servicebus_queue.ingest.name
          namespace    = azurerm_servicebus_namespace.main.name
          messageCount = "1"
        }
      }
    }
  }

  template {
    container {
      name   = "worker"
      image  = var.worker_image
      cpu    = 1
      memory = "2Gi"

      dynamic "env" {
        for_each = merge(local.common_env, {
          AZURE_CLIENT_ID = azurerm_user_assigned_identity.worker.client_id
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
