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

# The web interface in the basic tier: its own public address, the API told to allow it (CORS), the
# audio account told the same.
run "plan_basic_web_interface" {
  command = plan

  variables {
    web_client_id = "44444444-4444-4444-4444-444444444444"
    web_api_scope = "api://nodo/access"
  }

  assert {
    condition     = length(azurerm_container_app.web) == 1 && azurerm_container_app.web["primary"].ingress[0].external_enabled == true
    error_message = "the basic tier has one public web container"
  }

  assert {
    condition     = length(azurerm_api_management_api.web) == 0 && length(azurerm_cdn_frontdoor_route.web) == 0
    error_message = "the basic tier has no gateway and no edge to route through"
  }

  assert {
    condition     = length(azurerm_storage_account.main.blob_properties[0].cors_rule) == 1
    error_message = "the browser may upload audio from the web origin"
  }

  assert {
    condition     = contains([for e in azurerm_container_app.api["primary"].template[0].container[0].env : e.name], "NODO_CORS_ORIGINS")
    error_message = "the API allows the web origin when they are on different addresses"
  }

  assert {
    condition     = contains([for e in azurerm_container_app.web["primary"].template[0].container[0].env : e.name], "NODO_API_BASE_URL")
    error_message = "the web container is told where the API is"
  }
}
