# The real-time voice gateway (voice.tf, ADR-028): off by default, and when on, a session store that
# only speaks TLS, a second app per stamp that can lose a replica, and a route that keeps the
# Front-Door-only check. These assertions are the properties that must not regress.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "11111111-1111-1111-1111-111111111111"
      object_id       = "22222222-2222-2222-2222-222222222222"
      client_id       = "33333333-3333-3333-3333-333333333333"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }

  mock_data "azurerm_subscription" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000"
    }
  }
}
mock_provider "random" {}

variables {
  environment       = "prod"
  tier              = "professional"
  dr_location       = "northeurope"
  oidc_issuer       = "https://example.ciamlogin.com/tenant/v2.0"
  oidc_audience     = "api://nodo"
  postgres_sku_name = "GP_Standard_D4ds_v5"
  search_sku        = "standard"
  openai_deployments = {
    chat = { model_name = "gpt-4.1", model_version = "2025-04-14", sku_name = "DataZoneStandard", capacity = 50 }
  }
}

run "voice_is_off_by_default" {
  command = plan

  assert {
    condition = (
      length(azurerm_container_app.voice) == 0 &&
      length(azurerm_redis_cache.voice) == 0 &&
      length(azurerm_key_vault_secret.voice_redis_url) == 0 &&
      length(azurerm_key_vault_secret.voice_ticket_key) == 0 &&
      length(azurerm_private_endpoint.voice_redis) == 0 &&
      length(azurerm_api_management_api.voice) == 0 &&
      length(azurerm_api_management_api.voice_stream) == 0 &&
      length(azurerm_role_assignment.voice_speech) == 0
    )
    error_message = "without voice_enabled nothing of the voice gateway exists"
  }
}

run "voice_is_off_by_default_in_the_basic_tier" {
  command = plan

  variables {
    tier        = "basic"
    dr_location = null
  }

  assert {
    condition     = length(azurerm_container_app.voice) == 0 && length(azurerm_redis_cache.voice) == 0
    error_message = "without voice_enabled nothing of the voice gateway exists"
  }
}

run "plan_voice_professional" {
  command = plan

  variables {
    voice_enabled = true
  }

  assert {
    condition     = length(azurerm_container_app.voice) == 2 && length(azurerm_redis_cache.voice) == 2
    error_message = "a voice app and a session store per stamp"
  }

  # The store.
  assert {
    condition = alltrue([
      for k, v in azurerm_redis_cache.voice :
      v.non_ssl_port_enabled == false && v.minimum_tls_version == "1.2" && v.public_network_access_enabled == false
    ])
    error_message = "the session store speaks TLS only and has no public endpoint"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_redis_cache.voice : v.sku_name == "Premium" && length(v.zones) == 2])
    error_message = "the professional session store is zone redundant"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_redis_cache.voice : v.redis_configuration[0].maxmemory_policy == "noeviction"])
    error_message = "a session record must never be evicted under memory pressure"
  }

  assert {
    condition = (
      length(azurerm_private_endpoint.voice_redis) == 2 &&
      alltrue([for k, v in azurerm_private_endpoint.voice_redis : v.private_service_connection[0].subresource_names == tolist(["redisCache"])]) &&
      alltrue([for k, v in azurerm_private_dns_zone.voice_redis : v.name == "privatelink.redis.cache.windows.net"]) &&
      length(azurerm_private_dns_zone_virtual_network_link.voice_redis) == 2
    )
    error_message = "each stamp reaches its store through a private endpoint and its own private DNS zone"
  }

  # The app.
  assert {
    condition     = alltrue([for k, v in azurerm_container_app.voice : v.ingress[0].allow_insecure_connections == false && v.ingress[0].ip_security_restriction[0].name == "gateway-subnet"])
    error_message = "HTTPS only, and only the gateway subnet reaches the voice app"
  }

  assert {
    condition     = azurerm_container_app.voice["primary"].template[0].min_replicas >= 2
    error_message = "two replicas at least: a drain needs somewhere to hand sessions to"
  }

  assert {
    condition     = azurerm_container_app.voice["dr"].template[0].min_replicas == 0
    error_message = "the standby voice app is scaled to zero until the failover lever is set"
  }

  assert {
    condition = (
      azurerm_container_app.voice["primary"].template[0].termination_grace_period_seconds >= 25 + var.voice_drain_seconds &&
      azurerm_container_app.voice["primary"].template[0].termination_grace_period_seconds <= 600
    )
    error_message = "the grace period covers the 25 s connection close of serve.py plus the drain, within the platform maximum"
  }

  assert {
    condition     = azurerm_container_app.voice["primary"].template[0].tcp_scale_rule[0].concurrent_requests == "20"
    error_message = "scale out at 80 percent of the per-replica session cap (25)"
  }

  assert {
    condition = (
      contains([for e in azurerm_container_app.voice["primary"].template[0].container[0].env : e.name], "NODO_VOICE_ENABLED") &&
      contains([for e in azurerm_container_app.voice["primary"].template[0].container[0].env : e.name if e.secret_name == "voice-ticket-key"], "NODO_VOICE_TICKET_KEY") &&
      contains([for e in azurerm_container_app.voice["primary"].template[0].container[0].env : e.name if e.secret_name == "voice-redis-url"], "NODO_VOICE_REDIS_URL") &&
      !contains([for e in azurerm_container_app.voice["primary"].template[0].container[0].env : e.name if e.secret_name == null], "NODO_VOICE_REDIS_URL")
    )
    error_message = "the ticket key and the Redis URL reach the app as secret references, never as plain values"
  }

  assert {
    condition     = length(azurerm_role_assignment.voice_speech) == 2 && alltrue([for k, v in azurerm_role_assignment.voice_speech : v.role_definition_name == "Cognitive Services Speech User"])
    error_message = "the voice app may use Speech in each region, with the user role only"
  }

  # The route.
  assert {
    condition     = azurerm_api_management_api.voice_stream[0].api_type == "websocket" && azurerm_api_management_api.voice_stream[0].path == "v1/voice/stream" && azurerm_api_management_api.voice_stream[0].protocols == toset(["wss"])
    error_message = "the stream is a WebSocket API on /v1/voice/stream, secure only"
  }

  assert {
    condition     = azurerm_api_management_api.voice[0].path == "v1/voice/sessions" && azurerm_api_management_api.voice[0].subscription_required == false
    error_message = "the session calls have their own API beside the public one"
  }

  assert {
    condition     = !startswith(azurerm_api_management_api.voice[0].path, azurerm_api_management_api.voice_stream[0].path) && !startswith(azurerm_api_management_api.voice_stream[0].path, azurerm_api_management_api.voice[0].path)
    error_message = "the two voice APIs must not contain each other's path"
  }

  assert {
    condition     = length(azurerm_api_management_backend.voice) == 2
    error_message = "the session calls have a backend per region"
  }

  assert {
    condition     = contains(azurerm_cdn_frontdoor_route.main[0].patterns_to_match, "/v1/*")
    error_message = "the edge already carries /v1/voice/stream to the gateway: no route of its own"
  }
}

run "plan_voice_without_standby" {
  command = plan

  variables {
    voice_enabled = true
    dr_location   = null
  }

  assert {
    condition     = length(azurerm_container_app.voice) == 1 && length(azurerm_redis_cache.voice) == 1 && length(azurerm_api_management_backend.voice) == 1
    error_message = "no standby region, no standby voice stamp"
  }
}

run "plan_voice_failover" {
  command = plan

  variables {
    voice_enabled = true
    dr_active     = true
  }

  assert {
    condition     = azurerm_container_app.voice["dr"].template[0].min_replicas >= 2
    error_message = "the standby voice app serves from two replicas once the lever is set"
  }
}

run "plan_voice_basic" {
  command = plan

  variables {
    voice_enabled = true
    tier          = "basic"
    dr_location   = null
  }

  assert {
    condition     = length(azurerm_container_app.voice) == 1 && azurerm_container_app.voice["primary"].template[0].min_replicas >= 2
    error_message = "the basic tier runs the gateway too, from two replicas"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_redis_cache.voice : v.non_ssl_port_enabled == false && v.minimum_tls_version == "1.2" && v.sku_name == "Standard"])
    error_message = "the basic session store is still TLS only"
  }

  assert {
    condition     = length(azurerm_private_endpoint.voice_redis) == 0 && length(azurerm_api_management_api.voice_stream) == 0
    error_message = "the basic tier has no private network and no gateway"
  }
}

run "voice_needs_two_replicas" {
  command = plan

  variables {
    voice_enabled      = true
    voice_min_replicas = 1
  }

  expect_failures = [var.voice_min_replicas]
}

# The Redis URL and the policies are built from values the provider reports after an apply: stand in
# for them, so the strings can be read at plan time.
run "voice_urls_and_policies" {
  command = plan

  override_resource {
    target          = azurerm_redis_cache.voice
    override_during = plan
    values = {
      id                 = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Cache/Redis/redis-test"
      primary_access_key = "a+b/c=="
      hostname           = "redis-test.redis.cache.windows.net"
      ssl_port           = 6380
    }
  }

  override_resource {
    target          = azurerm_container_app.voice
    override_during = plan
    values = {
      ingress = { fqdn = "voice.example.internal" }
    }
  }

  variables {
    voice_enabled = true
  }

  assert {
    condition     = alltrue([for k, v in azurerm_key_vault_secret.voice_redis_url : v.value == nonsensitive(v.value) && startswith(nonsensitive(v.value), "rediss://:a%2Bb%2Fc%3D%3D@redis-test.redis.cache.windows.net:6380/")])
    error_message = "the stored Redis URL is rediss:// with the key encoded (the application refuses anything else on Azure)"
  }

  assert {
    condition = (
      strcontains(azurerm_api_management_api_policy.voice_stream[0].xml_content, "X-Azure-FDID") &&
      strcontains(azurerm_api_management_api_policy.voice_stream[0].xml_content, "rate-limit-by-key") &&
      !strcontains(azurerm_api_management_api_policy.voice_stream[0].xml_content, "validate-jwt")
    )
    error_message = "the stream keeps the Front-Door-only check and a rate limit; the credential is the ticket, not a header"
  }

  assert {
    condition = (
      strcontains(azurerm_api_management_api_policy.voice[0].xml_content, "X-Azure-FDID") &&
      strcontains(azurerm_api_management_api_policy.voice[0].xml_content, "validate-jwt") &&
      strcontains(azurerm_api_management_api_policy.voice[0].xml_content, "rate-limit-by-key")
    )
    error_message = "the session calls keep the Front-Door-only check, the token check and the per-person limit"
  }

  assert {
    condition     = strcontains(azurerm_api_management_api_policy.voice_stream[0].xml_content, "wss://voice.example.internal/v1/voice/stream")
    error_message = "the handshake is forwarded to the voice app as a secure WebSocket"
  }
}

run "voice_stream_policy_without_standby" {
  command = plan

  override_resource {
    target          = azurerm_container_app.voice
    override_during = plan
    values = {
      ingress = { fqdn = "voice.example.internal" }
    }
  }

  variables {
    voice_enabled = true
    dr_location   = null
  }

  assert {
    condition     = !strcontains(azurerm_api_management_api_policy.voice_stream[0].xml_content, "context.Deployment.Region")
    error_message = "with one region there is nothing to choose between"
  }
}
