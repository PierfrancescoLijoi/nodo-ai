# The professional tier is the architecture of docs/AZURE-ARCHITECTURE.md. These assertions are the
# properties that must not regress when somebody edits the module.

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
  alert_emails      = ["oncall@example.com"]
  enable_defender   = true
  lock_resources    = true
  openai_deployments = {
    chat = { model_name = "gpt-4.1", model_version = "2025-04-14", sku_name = "DataZoneStandard", capacity = 50 }
  }
}

run "plan_professional" {
  command = plan

  assert {
    condition     = length(azurerm_virtual_network.main) == 2
    error_message = "one network per region"
  }

  assert {
    condition     = length(azurerm_cdn_frontdoor_profile.main) == 1 && length(azurerm_api_management.main) == 1
    error_message = "the edge and the gateway exist"
  }

  assert {
    condition     = azurerm_cdn_frontdoor_firewall_policy.main[0].mode == "Prevention"
    error_message = "the firewall blocks by default"
  }

  assert {
    condition     = azurerm_container_app.api["primary"].template[0].min_replicas >= 3
    error_message = "at least one API replica per availability zone"
  }

  assert {
    condition     = azurerm_container_app_environment.main["primary"].zone_redundancy_enabled == true && azurerm_container_app_environment.main["primary"].internal_load_balancer_enabled == true
    error_message = "zone redundant and not reachable from the internet"
  }

  assert {
    condition     = azurerm_container_app.api["dr"].template[0].min_replicas == 0
    error_message = "the standby API is scaled to zero until the failover lever is set"
  }

  assert {
    condition     = !contains(keys(azurerm_container_app_job.worker), "dr")
    error_message = "the standby worker only exists while it serves traffic"
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.main.public_network_access_enabled == false
    error_message = "the database has no public endpoint"
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.main.high_availability[0].mode == "ZoneRedundant"
    error_message = "the database fails over between zones by itself"
  }

  assert {
    condition     = azurerm_servicebus_namespace.main.sku == "Premium" && azurerm_servicebus_namespace.main.public_network_access_enabled == false
    error_message = "private, premium queue"
  }

  assert {
    condition     = azurerm_storage_account.main.account_replication_type == "GZRS" && azurerm_storage_account.audit[0].public_network_access_enabled == false
    error_message = "geo-zone redundant audio, private audit archive"
  }

  assert {
    condition     = azurerm_storage_container_immutability_policy.audit[0].immutability_period_in_days >= 365
    error_message = "audit evidence is kept for at least a year"
  }

  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled == true
    error_message = "keys cannot be purged"
  }

  assert {
    condition     = length(azurerm_private_endpoint.main) >= 16
    error_message = "every data service has a private endpoint in each region"
  }

  assert {
    condition     = length(azurerm_monitor_scheduled_query_rules_alert_v2.burn) == 2
    error_message = "fast and slow burn-rate alerts exist"
  }
}

run "plan_professional_without_standby" {
  command = plan

  variables {
    dr_location = null
  }

  assert {
    condition     = length(azurerm_virtual_network.main) == 1 && length(azurerm_postgresql_flexible_server.replica) == 0
    error_message = "no standby region, no replica"
  }
}

run "plan_failover" {
  command = plan

  variables {
    dr_active           = true
    dr_promote_database = true
  }

  assert {
    condition     = azurerm_container_app.api["dr"].template[0].min_replicas >= 3
    error_message = "the standby API serves traffic from three replicas once the lever is set"
  }

  assert {
    condition     = contains(keys(azurerm_container_app_job.worker), "dr")
    error_message = "the standby worker exists once it serves traffic"
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.replica[0].replication_role == "None"
    error_message = "the replica is promoted"
  }
}

run "plan_edge_and_gateway_properties" {
  command = plan

  assert {
    condition     = !contains(azurerm_cdn_frontdoor_route.main[0].patterns_to_match, "/*")
    error_message = "the AI gateway must not be reachable through the edge"
  }

  assert {
    condition     = azurerm_storage_account.main.blob_properties[0].versioning_enabled == null || azurerm_storage_account.main.blob_properties[0].versioning_enabled == false
    error_message = "versions of an erased recording would keep it alive"
  }

  assert {
    condition     = length(azurerm_virtual_network_peering.to_dr) == 1 && length(azurerm_nat_gateway.main) == 2
    error_message = "the replica needs the peering and the gateway admits the workloads by NAT address"
  }

  assert {
    condition     = length([for k, v in azurerm_private_endpoint.main : k if endswith(k, "servicebus-dr")]) == 2
    error_message = "both networks can reach the standby queue namespace"
  }
}

run "plan_index_maintenance" {
  command = plan

  assert {
    condition     = contains(keys(azurerm_container_app_job.reindex), "primary") && contains(keys(azurerm_container_app_job.reindex), "dr")
    error_message = "the standby region can rebuild its own search index after a failover"
  }

  assert {
    condition     = azurerm_container_app_job.reindex["dr"].template[0].container[0].command[3] == "verify-index"
    error_message = "a reindex job started by mistake must only read"
  }

  assert {
    condition     = length(azurerm_monitor_scheduled_query_rules_alert_v2.index) == 1
    error_message = "a drifting index raises an alert"
  }

  assert {
    condition     = azurerm_role_assignment.identity["maintenance_search_primary"].role_definition_name == "Search Index Data Contributor"
    error_message = "maintenance writes to the index it repairs, in each region"
  }
}

run "plan_sweeps_and_audit_archive" {
  command = plan

  assert {
    condition     = azurerm_container_app_job.sweep.schedule_trigger_config[0].cron_expression == "*/30 * * * *"
    error_message = "lost work is looked for every half hour"
  }

  assert {
    condition     = length(azurerm_container_app_job.audit_archive) == 1
    error_message = "the audit trail leaves the database"
  }

  assert {
    condition     = azurerm_role_assignment.identity["audit_blob"].role_definition_name == "Storage Blob Data Contributor"
    error_message = "the archive writes to the immutable account"
  }

  assert {
    condition     = length(azurerm_monitor_scheduled_query_rules_alert_v2.audit_archive_silent) == 1
    error_message = "a silent archive is an alert"
  }
}

run "plan_first_connection_checks_and_the_two_switches" {
  command = plan

  variables {
    secrets_from_key_vault = false
    ai_token_limit_enabled = false
  }

  assert {
    condition     = length(azurerm_container_app_job.preflight) == 4
    error_message = "a first-connection check per region and identity"
  }

  assert {
    condition     = azurerm_container_app_job.preflight["primary-worker"].template[0].container[0].command[4] == "worker"
    error_message = "each check runs as its own identity"
  }
}

# Container Apps names stop at 32 characters, and "staging" is the longest environment name.
run "plan_names_fit_in_the_longest_environment" {
  command = plan

  # The suffix is random: fix it so the names can be measured at plan time.
  override_resource {
    target          = random_string.suffix
    override_during = plan
    values = {
      result = "abcd"
    }
  }

  variables {
    environment = "staging"
  }

  assert {
    condition = alltrue(concat(
      [for k, v in azurerm_container_app.api : length(v.name) <= 32],
      [for k, v in azurerm_container_app_job.worker : length(v.name) <= 32],
      [for k, v in azurerm_container_app_job.preflight : length(v.name) <= 32],
      [for k, v in azurerm_container_app_job.reindex : length(v.name) <= 32],
      [for v in [
        azurerm_container_app_job.migrate, azurerm_container_app_job.retention,
        azurerm_container_app_job.index_verify, azurerm_container_app_job.sweep,
        azurerm_container_app_job.audit_archive[0],
      ] : length(v.name) <= 32],
    ))
    error_message = "a Container Apps name is longer than 32 characters"
  }

  assert {
    condition     = length(azurerm_key_vault.main.name) <= 24
    error_message = "azurerm_key_vault.main.name is longer than 24 characters"
  }

  assert {
    condition     = length(azurerm_storage_account.main.name) <= 24
    error_message = "azurerm_storage_account.main.name is longer than 24 characters"
  }

  assert {
    condition     = length(azurerm_storage_account.audit[0].name) <= 24
    error_message = "azurerm_storage_account.audit[0].name is longer than 24 characters"
  }

  assert {
    condition     = length(azurerm_container_registry.main.name) <= 50
    error_message = "azurerm_container_registry.main.name is longer than 50 characters"
  }

  assert {
    condition     = length(azurerm_servicebus_namespace.main.name) <= 50
    error_message = "azurerm_servicebus_namespace.main.name is longer than 50 characters"
  }

  assert {
    condition     = length(azurerm_servicebus_namespace.dr[0].name) <= 50
    error_message = "azurerm_servicebus_namespace.dr[0].name is longer than 50 characters"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_user_assigned_identity.workload : length(v.name) <= 63])
    error_message = "an identity name becomes a database role name, which stops at 63 characters"
  }
}

# The web interface (ADR-027): a container per stamp behind the gateway, reached through its own route
# and rewrite, with the storage account told that the page may upload audio to it.
run "plan_web_interface" {
  command = plan

  variables {
    web_client_id = "44444444-4444-4444-4444-444444444444"
    web_api_scope = "api://nodo/access"
  }

  assert {
    condition     = length(azurerm_container_app.web) == 2 && azurerm_container_app.web["primary"].template[0].min_replicas >= 3
    error_message = "one web container per stamp, spread over the zones in the primary"
  }

  assert {
    condition     = azurerm_container_app.web["dr"].template[0].min_replicas == 0
    error_message = "the standby web container is scaled to zero until a failover"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_container_app.web : v.ingress[0].allow_insecure_connections == false])
    error_message = "the web container accepts HTTPS only"
  }

  assert {
    condition     = azurerm_container_app.web["primary"].ingress[0].ip_security_restriction[0].name == "gateway-subnet"
    error_message = "only the gateway subnet reaches the web container"
  }

  assert {
    condition     = alltrue(flatten([for k, v in azurerm_container_app.web : [for e in v.template[0].container[0].env : e.secret_name == null]]))
    error_message = "the web container holds no secret: it only publishes public sign-in settings"
  }

  assert {
    condition     = azurerm_cdn_frontdoor_route.web[0].patterns_to_match == tolist(["/*"]) && azurerm_cdn_frontdoor_route.main[0].patterns_to_match == tolist(["/v1/*", "/readyz"])
    error_message = "the API route keeps /v1 and the probe; the web route takes the rest"
  }

  assert {
    condition     = azurerm_cdn_frontdoor_rule.web_prefix[0].actions[0].url_rewrite_action[0].destination == "/ui/" && azurerm_cdn_frontdoor_rule.web_prefix[0].actions[0].url_rewrite_action[0].preserve_unmatched_path == true
    error_message = "web paths are sent to the gateway's web API under /ui"
  }

  assert {
    condition     = azurerm_api_management_api.web[0].path == "ui" && length(azurerm_api_management_api_operation.web) == 4
    error_message = "the web API has the root and the rest, for GET and HEAD"
  }

  assert {
    condition     = azurerm_api_management_api.web[0].path != azurerm_api_management_api.nodo[0].path && azurerm_api_management_api.web[0].subscription_required == false
    error_message = "the web API lives beside the public API, not in it"
  }

  assert {
    condition     = length(azurerm_storage_account.main.blob_properties[0].cors_rule) == 1 && azurerm_storage_account.main.blob_properties[0].cors_rule[0].allowed_methods == tolist(["PUT", "OPTIONS"])
    error_message = "the audio account lets the web origin upload (PUT) and nothing else"
  }

  assert {
    condition     = length([for k, v in azurerm_role_assignment.identity : k if v.role_definition_name == "AcrPull" && endswith(k, "web_acr_pull")]) == 1
    error_message = "the web identity may pull its image, and has no other role"
  }

  assert {
    condition     = length([for k, v in azurerm_role_assignment.identity : k if startswith(k, "web_")]) == 1
    error_message = "the web identity has exactly one grant"
  }

  assert {
    condition     = alltrue([for k, v in azurerm_container_app.web : length(v.name) <= 32])
    error_message = "a container app name stops at 32 characters"
  }
}

run "plan_web_interface_is_optional" {
  command = plan

  assert {
    condition     = length(azurerm_container_app.web) == 0 && length(azurerm_cdn_frontdoor_route.web) == 0 && length(azurerm_api_management_api.web) == 0
    error_message = "without web_client_id there is no web interface"
  }

  assert {
    condition     = length(azurerm_storage_account.main.blob_properties[0].cors_rule) == 0
    error_message = "without the web interface the audio account allows no browser origin"
  }
}

run "web_interface_must_be_allowed_by_the_api" {
  command = plan

  variables {
    web_client_id           = "44444444-4444-4444-4444-444444444444"
    web_api_scope           = "api://nodo/access"
    oidc_allowed_client_ids = ["55555555-5555-5555-5555-555555555555"]
  }

  expect_failures = [azurerm_container_app.web]
}
