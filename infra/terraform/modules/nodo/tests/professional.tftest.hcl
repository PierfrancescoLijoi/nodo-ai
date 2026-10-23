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
