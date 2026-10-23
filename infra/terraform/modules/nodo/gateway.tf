# API gateway (ADR-016). Everything that reaches the application passes here:
#   clients -> Front Door (WAF, TLS, DDoS) -> API Management -> Container Apps (internal)
#   the API and the worker -> API Management (AI gateway) -> Azure OpenAI in two regions
# The gateway is where authentication is checked first, where fairness is enforced per person and
# where the models are shared out, so the application behind it can stay simple.

locals {
  region_display = {
    swedencentral      = "Sweden Central"
    northeurope        = "North Europe"
    westeurope         = "West Europe"
    germanywestcentral = "Germany West Central"
    francecentral      = "France Central"
    norwayeast         = "Norway East"
    polandcentral      = "Poland Central"
    italynorth         = "Italy North"
    uksouth            = "UK South"
  }
  primary_region_display = lookup(local.region_display, var.location, var.location)
  dr_region_display      = var.dr_location == null ? "" : lookup(local.region_display, var.dr_location, var.dr_location)

  # Regional addresses, not the global one: the global name routes by latency and could send the
  # "primary" origin of Front Door to the standby region. Each stack also calls the gateway of its own region.
  gateway_host = {
    primary = local.pro ? replace(azurerm_api_management.main[0].gateway_regional_url, "https://", "") : ""
    dr      = local.dr ? replace(azurerm_api_management.main[0].additional_location[0].gateway_regional_url, "https://", "") : ""
  }
}

resource "azurerm_public_ip" "apim" {
  for_each            = local.regions
  name                = "pip-${local.name}-gateway-${each.key}"
  location            = each.value.location
  resource_group_name = local.rg_names[each.key]
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1", "2", "3"]
  domain_name_label   = "${local.short_name}-gw-${each.key}"
  tags                = local.tags
}

resource "azurerm_api_management" "main" {
  count                         = local.pro ? 1 : 0
  name                          = "apim-${local.short_name}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.main.name
  publisher_name                = var.project
  publisher_email               = var.apim_publisher_email
  sku_name                      = var.apim_sku_name
  virtual_network_type          = "External"
  public_ip_address_id          = azurerm_public_ip.apim["primary"].id
  public_network_access_enabled = true
  min_api_version               = "2021-08-01"
  client_certificate_enabled    = false
  tags                          = local.tags

  identity {
    type = "SystemAssigned"
  }

  virtual_network_configuration {
    subnet_id = azurerm_subnet.apim["primary"].id
  }

  dynamic "additional_location" {
    for_each = local.dr ? [1] : []
    content {
      location             = var.dr_location
      capacity             = 1
      public_ip_address_id = azurerm_public_ip.apim["dr"].id
      virtual_network_configuration {
        subnet_id = azurerm_subnet.apim["dr"].id
      }
    }
  }

  # Only strong protocols between the gateway and the world.
  security {
    backend_ssl30_enabled                               = false
    backend_tls10_enabled                               = false
    backend_tls11_enabled                               = false
    frontend_ssl30_enabled                              = false
    frontend_tls10_enabled                              = false
    frontend_tls11_enabled                              = false
    tls_ecdhe_ecdsa_with_aes128_cbc_sha_ciphers_enabled = false
    tls_ecdhe_ecdsa_with_aes256_cbc_sha_ciphers_enabled = false
    tls_ecdhe_rsa_with_aes128_cbc_sha_ciphers_enabled   = false
    tls_ecdhe_rsa_with_aes256_cbc_sha_ciphers_enabled   = false
    tls_rsa_with_aes128_cbc_sha256_ciphers_enabled      = false
    tls_rsa_with_aes128_cbc_sha_ciphers_enabled         = false
    tls_rsa_with_aes128_gcm_sha256_ciphers_enabled      = false
    tls_rsa_with_aes256_cbc_sha256_ciphers_enabled      = false
    tls_rsa_with_aes256_cbc_sha_ciphers_enabled         = false
    tls_rsa_with_aes256_gcm_sha384_ciphers_enabled      = false
    triple_des_ciphers_enabled                          = false
  }

  lifecycle {
    precondition {
      condition     = var.oidc_issuer != "" && var.oidc_audience != ""
      error_message = "The gateway validates tokens: set oidc_issuer and oidc_audience first."
    }
  }

  depends_on = [azurerm_subnet_network_security_group_association.apim]
}

# The gateway reaches the model accounts with its own identity.
resource "azurerm_role_assignment" "gateway_openai" {
  for_each = local.pro ? merge(
    { primary = azurerm_cognitive_account.openai.id },
    local.dr ? { dr = azurerm_cognitive_account.openai_dr[0].id } : {},
  ) : {}
  scope                = each.value
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_api_management.main[0].identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# --- named values: configuration the policies read -------------------------------------------

locals {
  gateway_values = local.pro ? {
    "tenant-id"     = local.tenant_id
    "oidc-issuer"   = var.oidc_issuer
    "oidc-audience" = var.oidc_audience
    "front-door-id" = azurerm_cdn_frontdoor_profile.main[0].resource_guid
  } : {}
}

resource "azurerm_api_management_named_value" "main" {
  for_each            = local.gateway_values
  name                = each.key
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main[0].name
  display_name        = each.key
  value               = each.value
}

# --- backends ---------------------------------------------------------------------------------
# A circuit breaker per backend: five server errors inside a minute and the gateway stops sending
# for 30 seconds (or for as long as the backend's Retry-After says), instead of piling on.

locals {
  backends = local.pro ? merge(
    {
      "api-primary"    = "https://${azurerm_container_app.api["primary"].ingress[0].fqdn}"
      "openai-primary" = "${trimsuffix(azurerm_cognitive_account.openai.endpoint, "/")}/openai"
    },
    local.dr ? {
      "api-dr"           = "https://${azurerm_container_app.api["dr"].ingress[0].fqdn}"
      "openai-secondary" = "${trimsuffix(azurerm_cognitive_account.openai_dr[0].endpoint, "/")}/openai"
    } : {},
  ) : {}
}

resource "azurerm_api_management_backend" "main" {
  for_each            = local.backends
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

# --- the public API ---------------------------------------------------------------------------

resource "azurerm_api_management_api" "nodo" {
  count                 = local.pro ? 1 : 0
  name                  = "nodo"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main[0].name
  revision              = "1"
  display_name          = "NODO AI"
  path                  = ""
  protocols             = ["https"]
  subscription_required = false # identity comes from the Entra token, not from a gateway key
}

resource "azurerm_api_management_api_operation" "nodo" {
  for_each            = local.pro ? toset(["GET", "POST", "PUT", "PATCH", "DELETE"]) : toset([])
  operation_id        = "proxy-${lower(each.key)}"
  api_name            = azurerm_api_management_api.nodo[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "${each.key} v1"
  method              = each.key
  url_template        = "/v1/*"
}

resource "azurerm_api_management_api_operation" "ready" {
  count               = local.pro ? 1 : 0
  operation_id        = "ready"
  api_name            = azurerm_api_management_api.nodo[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Readiness"
  method              = "GET"
  url_template        = "/readyz"
}

resource "azurerm_api_management_api_policy" "nodo" {
  count               = local.pro ? 1 : 0
  api_name            = azurerm_api_management_api.nodo[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  xml_content = templatefile("${path.module}/policies/api.xml.tftpl", {
    calls_per_minute       = var.rate_limit_calls_per_minute
    primary_region_display = local.primary_region_display
  })

  depends_on = [azurerm_api_management_named_value.main, azurerm_api_management_backend.main]
}

resource "azurerm_api_management_api_operation_policy" "ready" {
  count               = local.pro ? 1 : 0
  api_name            = azurerm_api_management_api.nodo[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  operation_id        = azurerm_api_management_api_operation.ready[0].operation_id
  xml_content = templatefile("${path.module}/policies/ready.xml.tftpl", {
    primary_region_display = local.primary_region_display
  })

  depends_on = [azurerm_api_management_named_value.main, azurerm_api_management_backend.main]
}

# --- the AI gateway ---------------------------------------------------------------------------

resource "azurerm_api_management_api" "ai" {
  count                 = local.pro ? 1 : 0
  name                  = "ai-gateway"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main[0].name
  revision              = "1"
  display_name          = "AI gateway"
  path                  = "openai"
  protocols             = ["https"]
  subscription_required = false
}

resource "azurerm_api_management_api_operation" "ai" {
  for_each            = local.pro ? toset(["GET", "POST"]) : toset([])
  operation_id        = "ai-${lower(each.key)}"
  api_name            = azurerm_api_management_api.ai[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "${each.key} model call"
  method              = each.key
  url_template        = "/*"
}

resource "azurerm_api_management_api_policy" "ai" {
  count               = local.pro ? 1 : 0
  api_name            = azurerm_api_management_api.ai[0].name
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  xml_content = templatefile("${path.module}/policies/ai.xml.tftpl", {
    tokens_per_minute      = var.ai_tokens_per_minute
    token_limit_enabled    = var.ai_token_limit_enabled
    primary_region_display = local.primary_region_display
    has_secondary          = local.dr
    caller_client_ids = [
      azurerm_user_assigned_identity.workload["api"].client_id,
      azurerm_user_assigned_identity.workload["worker"].client_id,
      azurerm_user_assigned_identity.workload["maintenance"].client_id,
    ]
  })

  depends_on = [azurerm_api_management_named_value.main, azurerm_api_management_backend.main]
}

# --- telemetry from the gateway ---------------------------------------------------------------

resource "azurerm_api_management_logger" "appinsights" {
  count               = local.pro ? 1 : 0
  name                = "appinsights"
  api_management_name = azurerm_api_management.main[0].name
  resource_group_name = azurerm_resource_group.main.name
  resource_id         = azurerm_application_insights.main.id

  application_insights {
    instrumentation_key = azurerm_application_insights.main.instrumentation_key
  }
}

resource "azurerm_api_management_diagnostic" "appinsights" {
  count                    = local.pro ? 1 : 0
  identifier               = "applicationinsights"
  resource_group_name      = azurerm_resource_group.main.name
  api_management_name      = azurerm_api_management.main[0].name
  api_management_logger_id = azurerm_api_management_logger.appinsights[0].id

  sampling_percentage       = 25
  always_log_errors         = true
  log_client_ip             = false # personal data; the WAF logs hold addresses for the security team
  verbosity                 = "information"
  http_correlation_protocol = "W3C"
}
