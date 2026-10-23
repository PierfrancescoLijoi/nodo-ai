# The basic tier must stay cheap and public: no network, no gateway, no second region.

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
  environment   = "dev"
  tier          = "basic"
  oidc_issuer   = "https://example.ciamlogin.com/tenant/v2.0"
  oidc_audience = "api://nodo"
}

run "plan_basic" {
  command = plan

  assert {
    condition     = length(azurerm_virtual_network.main) == 0
    error_message = "the basic tier has no virtual network"
  }

  assert {
    condition     = length(azurerm_api_management.main) == 0 && length(azurerm_cdn_frontdoor_profile.main) == 0
    error_message = "the basic tier has no gateway and no edge"
  }

  assert {
    condition     = azurerm_container_app.api["primary"].template[0].min_replicas == 1
    error_message = "the basic tier runs one replica"
  }

  assert {
    condition     = azurerm_storage_account.main.account_replication_type == "LRS"
    error_message = "the basic tier uses locally redundant storage"
  }

  assert {
    condition     = azurerm_storage_account.main.shared_access_key_enabled == false
    error_message = "no shared keys on storage, in any tier"
  }

  assert {
    condition     = length(azurerm_container_app_job.audit_archive) == 0
    error_message = "the basic tier has no immutable audit account to archive to"
  }
}
