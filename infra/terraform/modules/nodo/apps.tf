# Compute. Basic tier: one environment with a public API. Professional tier: one environment per
# region stamp, zone redundant, inside the virtual network, reached only through the API gateway.

locals {
  stacks = local.pro ? local.regions : { primary = { location = var.location, cidr = "" } }

  api_scale = {
    primary = { min = local.pro ? max(3, var.api_min_replicas) : var.api_min_replicas, max = var.api_max_replicas }
    dr      = { min = var.dr_active ? 3 : 0, max = var.api_max_replicas }
  }

  # Regional differences: the standby stack reads the replica and builds its own search index.
  endpoints = {
    primary = {
      postgres = azurerm_postgresql_flexible_server.main.fqdn
      search   = "https://${azurerm_search_service.main.name}.search.windows.net"
      speech   = azurerm_cognitive_account.speech.endpoint
      openai   = azurerm_cognitive_account.openai.endpoint
    }
    dr = {
      postgres = try(azurerm_postgresql_flexible_server.replica[0].fqdn, "")
      search   = try("https://${azurerm_search_service.dr[0].name}.search.windows.net", "")
      speech   = try(azurerm_cognitive_account.speech_dr[0].endpoint, "")
      openai   = try(azurerm_cognitive_account.openai_dr[0].endpoint, "")
    }
  }

  # In the professional tier the model calls go through the AI gateway (gateway.tf), which spreads
  # them over both regions and enforces the token budget; the callers keep their Entra identity.
  openai_endpoint = {
    for k in keys(local.stacks) : k => local.pro ? "https://${local.gateway_host[k]}" : local.endpoints[k].openai
  }

  # The Azure environment (dev, staging, prod) differs from NODO_ENVIRONMENT: in Azure the API always
  # runs with NODO_ENVIRONMENT=prod so dev tokens, demo data and weak keys stay refused.
  common_env = {
    for k in keys(local.stacks) : k => {
      NODO_ENVIRONMENT                      = "prod"
      NODO_AZURE_ENVIRONMENT                = var.environment
      NODO_BACKEND                          = "azure"
      NODO_AUTH_MODE                        = "oidc"
      NODO_OIDC_ISSUER                      = var.oidc_issuer
      NODO_OIDC_AUDIENCE                    = var.oidc_audience
      NODO_OIDC_ALLOWED_CLIENT_IDS          = join(",", var.oidc_allowed_client_ids)
      NODO_AZURE_STORAGE_ACCOUNT            = azurerm_storage_account.main.name
      NODO_AZURE_SERVICEBUS_FQDN            = local.servicebus_fqdn
      NODO_AZURE_SERVICEBUS_QUEUE           = azurerm_servicebus_queue.ingest.name
      NODO_AZURE_POSTGRES_HOST              = local.endpoints[k].postgres
      NODO_AZURE_POSTGRES_DATABASE          = azurerm_postgresql_flexible_server_database.nodo.name
      NODO_AZURE_SEARCH_ENDPOINT            = local.endpoints[k].search
      NODO_AZURE_OPENAI_ENDPOINT            = local.openai_endpoint[k]
      NODO_AZURE_SPEECH_ENDPOINT            = local.endpoints[k].speech
      NODO_AZURE_DB_ROLES                   = join(",", [azurerm_user_assigned_identity.workload["api"].name, azurerm_user_assigned_identity.workload["worker"].name])
      NODO_AZURE_RETENTION_ROLES            = azurerm_user_assigned_identity.workload["retention"].name
      NODO_AZURE_MAINTENANCE_ROLES          = azurerm_user_assigned_identity.workload["maintenance"].name
      NODO_OPENAI_CHAT_DEPLOYMENT           = var.openai_chat_deployment
      NODO_OPENAI_EMBEDDING_DEPLOYMENT      = var.openai_embedding_deployment
      NODO_OPENAI_EMBEDDING_DIMENSIONS      = tostring(var.embedding_dimensions)
      NODO_TELEMETRY_SAMPLING_RATIO         = local.pro ? "0.25" : "1.0"
      APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    }
  }

  # The alias keeps the same name across a Service Bus failover, so no application restart is needed.
  servicebus_fqdn = local.dr ? "${azurerm_servicebus_namespace_disaster_recovery_config.main[0].name}.servicebus.windows.net" : "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
}

resource "azurerm_container_app_environment" "main" {
  for_each                           = local.stacks
  name                               = "cae-${local.name}-${each.key}"
  location                           = each.value.location
  resource_group_name                = local.rg_names[each.key]
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id           = local.pro ? azurerm_subnet.aca[each.key].id : null
  infrastructure_resource_group_name = local.pro ? "rg-${local.name}-${each.key}-aca" : null
  internal_load_balancer_enabled     = local.pro ? true : null
  zone_redundancy_enabled            = local.pro ? true : null
  tags                               = local.tags

  dynamic "workload_profile" {
    for_each = local.pro ? [1] : []
    content {
      name                  = "Consumption"
      workload_profile_type = "Consumption"
    }
  }
}

# Inside the network, the environment's names resolve to its internal load balancer.
resource "azurerm_private_dns_zone" "aca" {
  for_each            = local.pro ? local.stacks : {}
  name                = azurerm_container_app_environment.main[each.key].default_domain
  resource_group_name = local.rg_names[each.key]
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "aca" {
  for_each              = local.pro ? local.stacks : {}
  name                  = "link-${each.key}"
  resource_group_name   = local.rg_names[each.key]
  private_dns_zone_name = azurerm_private_dns_zone.aca[each.key].name
  virtual_network_id    = azurerm_virtual_network.main[each.key].id
  tags                  = local.tags
}

resource "azurerm_private_dns_a_record" "aca" {
  for_each            = local.pro ? local.stacks : {}
  name                = "*"
  zone_name           = azurerm_private_dns_zone.aca[each.key].name
  resource_group_name = local.rg_names[each.key]
  ttl                 = 300
  records             = [azurerm_container_app_environment.main[each.key].static_ip_address]
}

resource "azurerm_container_app" "api" {
  for_each                     = local.stacks
  name                         = "ca-${local.name}-api-${each.key}"
  container_app_environment_id = azurerm_container_app_environment.main[each.key].id
  resource_group_name          = local.rg_names[each.key]
  revision_mode                = "Single"
  workload_profile_name        = local.pro ? "Consumption" : null
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["api"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["api"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["api"].id
  }

  secret {
    name                = "telemetry-salt"
    key_vault_secret_id = azurerm_key_vault_secret.telemetry_salt.versionless_id
    identity            = azurerm_user_assigned_identity.workload["api"].id
  }

  ingress {
    external_enabled           = true # on the internal load balancer in the professional tier
    target_port                = var.api_target_port
    transport                  = "auto"
    allow_insecure_connections = false

    dynamic "ip_security_restriction" {
      for_each = local.pro ? [1] : []
      content {
        name             = "gateway-subnet"
        action           = "Allow"
        ip_address_range = local.subnet_cidrs[each.key].apim
      }
    }

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas                     = local.api_scale[each.key].min
    max_replicas                     = local.api_scale[each.key].max
    termination_grace_period_seconds = 30

    http_scale_rule {
      name                = "http"
      concurrent_requests = "50" # CAPACITY-MODEL.md: a replica sustains far more, this keeps latency flat
    }

    container {
      name   = "api"
      image  = var.api_image
      cpu    = var.api_vcpu
      memory = var.api_memory

      dynamic "env" {
        for_each = merge(local.common_env[each.key], {
          AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["api"].client_id
          NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["api"].name
          WEB_CONCURRENCY          = tostring(max(1, ceil(var.api_vcpu)))
          FORWARDED_ALLOW_IPS      = local.pro ? local.subnet_cidrs[each.key].aca : "127.0.0.1"
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

      env {
        name        = "NODO_TELEMETRY_SALT"
        secret_name = "telemetry-salt"
      }

      startup_probe {
        transport               = "HTTP"
        path                    = "/healthz"
        port                    = var.api_target_port
        interval_seconds        = 3
        failure_count_threshold = 20
      }

      liveness_probe {
        transport               = "HTTP"
        path                    = "/healthz"
        port                    = var.api_target_port
        interval_seconds        = 10
        failure_count_threshold = 3
      }

      readiness_probe {
        transport               = "HTTP"
        path                    = "/readyz"
        port                    = var.api_target_port
        interval_seconds        = 5
        failure_count_threshold = 3
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    # The deploy pipeline rolls out new images; Terraform owns everything else.
    ignore_changes = [template[0].container[0].image]
  }
}

# Ingestion worker: starts when the queue has messages and scales to zero otherwise. The standby
# stack gets one only while it serves traffic.
resource "azurerm_container_app_job" "worker" {
  for_each                     = { for k, v in local.stacks : k => v if k == "primary" || var.dr_active }
  name                         = "caj-${local.name}-worker-${each.key}"
  location                     = each.value.location
  resource_group_name          = local.rg_names[each.key]
  container_app_environment_id = azurerm_container_app_environment.main[each.key].id
  workload_profile_name        = local.pro ? "Consumption" : null
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 1
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["worker"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["worker"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["worker"].id
  }

  secret {
    name                = "telemetry-salt"
    key_vault_secret_id = azurerm_key_vault_secret.telemetry_salt.versionless_id
    identity            = azurerm_user_assigned_identity.workload["worker"].id
  }

  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = var.worker_max_executions
      polling_interval_in_seconds = 15

      rules {
        name             = "ingest-queue"
        custom_rule_type = "azure-servicebus"
        identity_id      = azurerm_user_assigned_identity.workload["worker"].id
        metadata = {
          queueName    = azurerm_servicebus_queue.ingest.name
          namespace    = local.dr ? azurerm_servicebus_namespace_disaster_recovery_config.main[0].name : azurerm_servicebus_namespace.main.name
          messageCount = "1"
        }
      }
    }
  }

  template {
    container {
      name    = "worker"
      image   = var.api_image
      command = ["python", "-m", "nodo_api.worker"]
      cpu     = 1
      memory  = "2Gi"

      dynamic "env" {
        for_each = merge(local.common_env[each.key], {
          AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["worker"].client_id
          NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["worker"].name
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

      env {
        name        = "NODO_TELEMETRY_SALT"
        secret_name = "telemetry-salt"
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# Database and search bootstrap: started explicitly by the deploy pipeline before rollout.
resource "azurerm_container_app_job" "migrate" {
  name                         = "caj-${local.name}-migrate"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main["primary"].id
  workload_profile_name        = local.pro ? "Consumption" : null
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 0
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["migrate"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["migrate"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["migrate"].id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "migrate"
      image   = var.api_image
      command = ["python", "-m", "nodo_api.admin", "bootstrap"]
      cpu     = 1
      memory  = "2Gi"

      dynamic "env" {
        for_each = merge(local.common_env["primary"], {
          AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["migrate"].client_id
          NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["migrate"].name
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
    }
  }

  depends_on = [
    azurerm_postgresql_flexible_server_active_directory_administrator.migrate,
    azurerm_role_assignment.identity,
  ]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# Per-meeting audio retention (ADR-019): every six hours, under a database role that can see
# which meetings are due and nothing else. A meeting it cannot clean fails the job, which alerts.
resource "azurerm_container_app_job" "retention" {
  name                         = "caj-${local.name}-retention"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main["primary"].id
  workload_profile_name        = local.pro ? "Consumption" : null
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["retention"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["retention"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["retention"].id
  }

  schedule_trigger_config {
    cron_expression          = "15 */6 * * *"
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "retention"
      image   = var.api_image
      command = ["python", "-m", "nodo_api.admin", "retention"]
      cpu     = 0.5
      memory  = "1Gi"

      dynamic "env" {
        for_each = merge(local.common_env["primary"], {
          AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["retention"].client_id
          NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["retention"].name
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
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# Index maintenance (ADR-021). The weekly run only reads: it compares the search index and the graph
# with the database and fails when they differ, which raises an alert. The reindex job is started by
# hand (after an outage, a restore, or in the standby region after a failover; RUNBOOKS.md) with the
# repair command; its default command is the read-only one, so a stray start changes nothing.
locals {
  maintenance_env = {
    for k in keys(local.stacks) : k => merge(local.common_env[k], {
      AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["maintenance"].client_id
      NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["maintenance"].name
    })
  }
}

resource "azurerm_container_app_job" "index_verify" {
  name                         = "caj-${local.name}-index-verify"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main["primary"].id
  workload_profile_name        = local.pro ? "Consumption" : null
  replica_timeout_in_seconds   = 3600
  replica_retry_limit          = 0
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["maintenance"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["maintenance"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["maintenance"].id
  }

  schedule_trigger_config {
    cron_expression          = "30 3 * * 0"
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "index-verify"
      image   = var.api_image
      command = ["python", "-m", "nodo_api.admin", "verify-index"]
      cpu     = 0.5
      memory  = "1Gi"

      dynamic "env" {
        for_each = local.maintenance_env["primary"]
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "NODO_ATTESTATION_HMAC_KEY"
        secret_name = "attestation-hmac-key"
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

resource "azurerm_container_app_job" "reindex" {
  for_each                     = local.stacks
  name                         = "caj-${local.name}-reindex-${each.key}"
  location                     = each.value.location
  resource_group_name          = local.rg_names[each.key]
  container_app_environment_id = azurerm_container_app_environment.main[each.key].id
  workload_profile_name        = local.pro ? "Consumption" : null
  replica_timeout_in_seconds   = 14400
  replica_retry_limit          = 0
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload["maintenance"].id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload["maintenance"].id
  }

  secret {
    name                = "attestation-hmac-key"
    key_vault_secret_id = azurerm_key_vault_secret.attestation_key.versionless_id
    identity            = azurerm_user_assigned_identity.workload["maintenance"].id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "reindex"
      image   = var.api_image
      command = ["python", "-m", "nodo_api.admin", "verify-index"]
      cpu     = 1
      memory  = "2Gi"

      dynamic "env" {
        for_each = local.maintenance_env[each.key]
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "NODO_ATTESTATION_HMAC_KEY"
        secret_name = "attestation-hmac-key"
      }
    }
  }

  depends_on = [azurerm_role_assignment.identity]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
