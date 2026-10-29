# Real-time voice gateway (ADR-028, VOICE-REALTIME-ARCHITECTURE.md). Off unless voice_enabled:
#   clients -> Front Door -> API Management (WebSocket API) -> Container Apps (voice) -> Redis
# The voice app is the API image started with NODO_VOICE_ENABLED. It is a separate app, not a setting
# of the API app, because its replicas are long-lived (a socket lives for minutes to hours), scale on
# connections and need a termination grace period long enough to hand sessions over. The REST calls
# that open a session and ask for a ticket live in the same router, so they are sent to it as well.
# Front Door needs no new route: /v1/* already reaches the gateway, which tells the voice paths apart.

locals {
  voice        = var.voice_enabled
  voice_stacks = local.voice ? local.stacks : {}

  # uvicorn gives connections 25 s to close (serve.py), then the lifespan hands the sessions over for
  # voice_drain_seconds; the margin covers the Redis round trips of the hand-over.
  voice_grace_seconds = 25 + var.voice_drain_seconds + 15

  voice_scale = {
    primary = { min = var.voice_min_replicas, max = var.voice_max_replicas }
    dr      = { min = var.dr_active ? var.voice_min_replicas : 0, max = var.voice_max_replicas }
  }

  voice_secret_names = toset(["attestation-hmac-key", "telemetry-salt", "voice-ticket-key", "voice-redis-url"])

  # Same two ways of handing a secret over as in apps.tf: by Key Vault reference, or inline.
  voice_secret_references = {
    for k in keys(local.voice_stacks) : k => {
      "attestation-hmac-key" = azurerm_key_vault_secret.attestation_key.versionless_id
      "telemetry-salt"       = azurerm_key_vault_secret.telemetry_salt.versionless_id
      "voice-ticket-key"     = azurerm_key_vault_secret.voice_ticket_key[0].versionless_id
      "voice-redis-url"      = azurerm_key_vault_secret.voice_redis_url[k].versionless_id
    }
  }
  voice_secret_values = {
    for k in keys(local.voice_stacks) : k => {
      "attestation-hmac-key" = var.secrets_from_key_vault ? "" : random_password.attestation_key.result
      "telemetry-salt"       = var.secrets_from_key_vault ? "" : random_password.telemetry_salt.result
      "voice-ticket-key"     = var.secrets_from_key_vault ? "" : random_password.voice_ticket_key[0].result
      "voice-redis-url"      = var.secrets_from_key_vault ? "" : local.voice_redis_url[k]
    }
  }

  # rediss:// only: the application refuses anything else on Azure, and the non-TLS port is off.
  voice_redis_url = {
    for k, v in azurerm_redis_cache.voice : k => "rediss://:${urlencode(v.primary_access_key)}@${v.hostname}:${v.ssl_port}/0"
  }
}

# --- secrets ----------------------------------------------------------------------------------

# Signs the one-use tickets (HMAC). Replicas of both stamps share it, so a ticket issued by one
# replica is accepted by the one that takes the session over.
resource "random_password" "voice_ticket_key" {
  count   = local.voice ? 1 : 0
  length  = 64
  special = false
}

resource "azurerm_key_vault_secret" "voice_ticket_key" {
  count        = local.voice ? 1 : 0
  name         = "voice-ticket-key"
  value        = random_password.voice_ticket_key[0].result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_officer]
}

resource "azurerm_key_vault_secret" "voice_redis_url" {
  for_each     = local.voice_stacks
  name         = "voice-redis-url-${each.key}"
  value        = local.voice_redis_url[each.key]
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_officer]
}

# --- session store ----------------------------------------------------------------------------
# Leases, admission counters, acknowledged offsets and the finals of live meetings (text, for six
# hours at most). One cache per stamp: a session never moves between regions.

resource "azurerm_redis_cache" "voice" {
  for_each                      = local.voice_stacks
  name                          = "redis-${local.short_name}-${each.key}"
  location                      = each.value.location
  resource_group_name           = local.rg_names[each.key]
  capacity                      = var.voice_redis_capacity
  family                        = local.pro ? "P" : "C"
  sku_name                      = local.pro ? "Premium" : "Standard"
  non_ssl_port_enabled          = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = !local.pro # basic tier: public, TLS and an access key; professional: private endpoint only
  zones                         = local.pro ? ["1", "2"] : null
  tags                          = local.tags

  redis_configuration {
    # A session record that is evicted under memory pressure is a lost meeting: refuse writes instead.
    maxmemory_policy = "noeviction"
  }
}

resource "azurerm_private_dns_zone" "voice_redis" {
  for_each            = local.pro ? local.voice_stacks : {}
  name                = "privatelink.redis.cache.windows.net"
  resource_group_name = local.rg_names[each.key]
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "voice_redis" {
  for_each              = local.pro ? local.voice_stacks : {}
  name                  = "link-${each.key}"
  resource_group_name   = local.rg_names[each.key]
  private_dns_zone_name = azurerm_private_dns_zone.voice_redis[each.key].name
  virtual_network_id    = azurerm_virtual_network.main[each.key].id
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "voice_redis" {
  for_each            = local.pro ? local.voice_stacks : {}
  name                = "pe-${local.name}-${each.key}-voice-redis"
  location            = each.value.location
  resource_group_name = local.rg_names[each.key]
  subnet_id           = azurerm_subnet.endpoint[each.key].id
  tags                = local.tags

  private_service_connection {
    name                           = "voice-redis"
    private_connection_resource_id = azurerm_redis_cache.voice[each.key].id
    subresource_names              = ["redisCache"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.voice_redis[each.key].id]
  }
}

# --- the voice app ----------------------------------------------------------------------------
# It runs as the API's identity: the same database role, the same search and model grants, plus the
# Speech grant below. A dedicated identity would be tighter, but it needs a database role of its own
# (NODO_AZURE_DB_ROLES) and a change to main.tf and rbac.tf; see ADR-028.

resource "azurerm_role_assignment" "voice_speech" {
  for_each             = local.voice ? local.regional.speech : {}
  scope                = each.value
  role_definition_name = "Cognitive Services Speech User"
  principal_id         = local.principal["api"]
  principal_type       = "ServicePrincipal"
}

resource "azurerm_container_app" "voice" {
  for_each                     = local.voice_stacks
  name                         = "ca-${local.name}-voice-${each.key}"
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

  dynamic "secret" {
    for_each = local.voice_secret_names
    content {
      name                = secret.key
      key_vault_secret_id = var.secrets_from_key_vault ? local.voice_secret_references[each.key][secret.key] : null
      identity            = var.secrets_from_key_vault ? azurerm_user_assigned_identity.workload["api"].id : null
      value               = var.secrets_from_key_vault ? null : local.voice_secret_values[each.key][secret.key]
    }
  }

  ingress {
    external_enabled           = true # on the internal load balancer in the professional tier
    target_port                = var.api_target_port
    transport                  = "auto" # WebSocket upgrades pass through the environment's proxy
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
    min_replicas                     = local.voice_scale[each.key].min
    max_replicas                     = local.voice_scale[each.key].max
    termination_grace_period_seconds = local.voice_grace_seconds

    # A socket is a TCP connection: scale on how many each replica holds, before it reaches its own cap.
    tcp_scale_rule {
      name                = "connections"
      concurrent_requests = tostring(floor(var.voice_sessions_per_replica * 0.8))
    }

    container {
      name   = "voice"
      image  = var.api_image
      cpu    = var.voice_vcpu
      memory = var.voice_memory

      dynamic "env" {
        for_each = merge(local.common_env[each.key], {
          AZURE_CLIENT_ID          = azurerm_user_assigned_identity.workload["api"].client_id
          NODO_AZURE_POSTGRES_USER = azurerm_user_assigned_identity.workload["api"].name
          # One process: the session hub is per process, and the per-replica cap is meant per process.
          WEB_CONCURRENCY                     = "1"
          FORWARDED_ALLOW_IPS                 = local.pro ? local.subnet_cidrs[each.key].aca : "127.0.0.1"
          NODO_VOICE_ENABLED                  = "true"
          NODO_VOICE_MAX_SESSIONS_PER_REPLICA = tostring(var.voice_sessions_per_replica)
          NODO_VOICE_DRAIN_SECONDS            = tostring(var.voice_drain_seconds)
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

      env {
        name        = "NODO_VOICE_TICKET_KEY"
        secret_name = "voice-ticket-key"
      }

      env {
        name        = "NODO_VOICE_REDIS_URL"
        secret_name = "voice-redis-url"
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

  depends_on = [azurerm_role_assignment.identity, azurerm_role_assignment.voice_speech]

  lifecycle {
    # The deploy pipeline rolls out new images; Terraform owns everything else.
    ignore_changes = [template[0].container[0].image]
  }
}

# --- the route: API Management ---------------------------------------------------------------
# Front Door cannot reach the environment's internal load balancer without a private link approval
# that Terraform can not complete, so the voice paths go the way the rest of the API goes. Two APIs
# with paths that do not contain each other: the REST calls (token checked here) and the WebSocket
# (ticket checked by the application). Both are narrower than the public API, whose path is empty.

locals {
  voice_apim = local.pro && local.voice
  voice_backends = local.voice_apim ? merge(
    { "voice-primary" = "https://${azurerm_container_app.voice["primary"].ingress[0].fqdn}/v1/voice/sessions" },
    local.dr ? { "voice-dr" = "https://${azurerm_container_app.voice["dr"].ingress[0].fqdn}/v1/voice/sessions" } : {},
  ) : {}
  voice_stream_url = {
    primary = local.voice_apim ? "wss://${azurerm_container_app.voice["primary"].ingress[0].fqdn}/v1/voice/stream" : ""
    dr      = local.voice_apim && local.dr ? "wss://${azurerm_container_app.voice["dr"].ingress[0].fqdn}/v1/voice/stream" : ""
  }
}

resource "azurerm_api_management_backend" "voice" {
  for_each            = local.voice_backends
  name                = each.key
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main[0].name
  protocol            = "http"
  url                 = each.value

  circuit_breaker_rule {
    name                       = "server-errors"
    trip_duration              = "PT30S"
    accept_retry_after_enabled = true

    failure_condition {
      count             = 5
      interval_duration = "PT1M"

      status_code_range {
        min = 500
        max = 599
      }
    }
  }
}

resource "azurerm_api_management_api" "voice" {
  count                 = local.voice_apim ? 1 : 0
  name                  = "voice"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main[0].name
  revision              = "1"
  display_name          = "NODO voice sessions"
  path                  = "v1/voice/sessions"
  protocols             = ["https"]
  subscription_required = false # identity comes from the Entra token, as for the public API
}

resource "azurerm_api_management_api_operation" "voice_open" {
  count               = local.voice_apim ? 1 : 0
  operation_id        = "open-session"
  api_name            = azurerm_api_management_api.voice[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Open a voice session"
  method              = "POST"
  url_template        = "/"
}

resource "azurerm_api_management_api_operation" "voice_ticket" {
  count               = local.voice_apim ? 1 : 0
  operation_id        = "session-ticket"
  api_name            = azurerm_api_management_api.voice[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Ask for a WebSocket ticket"
  method              = "POST"
  url_template        = "/{session_id}/tickets"

  template_parameter {
    name     = "session_id"
    required = true
    type     = "string"
  }
}

resource "azurerm_api_management_api_policy" "voice" {
  count               = local.voice_apim ? 1 : 0
  api_name            = azurerm_api_management_api.voice[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  xml_content = templatefile("${path.module}/policies/voice.xml.tftpl", {
    calls_per_minute       = var.rate_limit_calls_per_minute
    primary_region_display = local.primary_region_display
  })

  depends_on = [azurerm_api_management_named_value.main, azurerm_api_management_backend.voice]
}

# The WebSocket itself. Its only operation, onHandshake, is created by the service; the API-level
# policy applies to it. The service URL is the primary stamp's; the policy picks the stamp of the
# region the handshake arrived in.
resource "azurerm_api_management_api" "voice_stream" {
  count                 = local.voice_apim ? 1 : 0
  name                  = "voice-stream"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main[0].name
  revision              = "1"
  display_name          = "NODO voice stream"
  path                  = "v1/voice/stream"
  protocols             = ["wss"]
  service_url           = local.voice_stream_url["primary"]
  api_type              = "websocket"
  subscription_required = false # the ticket in the first message is the credential
}

resource "azurerm_api_management_api_policy" "voice_stream" {
  count               = local.voice_apim ? 1 : 0
  api_name            = azurerm_api_management_api.voice_stream[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  xml_content = templatefile("${path.module}/policies/voice_stream.xml.tftpl", {
    connects_per_minute    = var.voice_connects_per_minute
    primary_region_display = local.primary_region_display
    primary_url            = local.voice_stream_url["primary"]
    standby_url            = local.voice_stream_url["dr"]
  })

  depends_on = [azurerm_api_management_named_value.main]
}

output "voice_app_names" {
  description = "Voice gateway app per region stamp (null while voice_enabled is false, so the plan output does not change)."
  value       = local.voice ? { for k, v in azurerm_container_app.voice : k => v.name } : null
}

output "voice_fqdns" {
  description = "Address of the voice gateway per stamp. Basic tier: the public address clients open the WebSocket on. Professional tier: reached through the gateway, not directly. Null while voice_enabled is false."
  value       = local.voice ? { for k, v in azurerm_container_app.voice : k => v.ingress[0].fqdn } : null
}
