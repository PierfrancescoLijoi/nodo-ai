# Edge (ADR-016): the one public entrance. TLS ends here, the WAF inspects every request, the global
# network carries it to the nearest healthy gateway, and a failing region is routed around within
# a probe interval or two (about thirty seconds).

resource "azurerm_cdn_frontdoor_profile" "main" {
  count                    = local.pro ? 1 : 0
  name                     = "afd-${local.name}"
  resource_group_name      = azurerm_resource_group.main.name
  sku_name                 = "Premium_AzureFrontDoor"
  response_timeout_seconds = 60
  tags                     = local.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "main" {
  count                    = local.pro ? 1 : 0
  name                     = "ep-${local.short_name}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main[0].id
  tags                     = local.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "gateway" {
  count                    = local.pro ? 1 : 0
  name                     = "gateway"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main[0].id

  session_affinity_enabled                                  = false
  restore_traffic_time_to_healed_or_new_endpoint_in_minutes = 5

  health_probe {
    path                = "/readyz"
    protocol            = "Https"
    request_type        = "GET"
    interval_in_seconds = 30
  }

  load_balancing {
    sample_size                        = 4
    successful_samples_required        = 3
    additional_latency_in_milliseconds = 50
  }
}

# Primary region first; the standby takes over only while the primary fails its probes.
resource "azurerm_cdn_frontdoor_origin" "primary" {
  count                          = local.pro ? 1 : 0
  name                           = "primary"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.gateway[0].id
  enabled                        = true
  host_name                      = local.gateway_host["primary"]
  origin_host_header             = local.gateway_host["primary"]
  https_port                     = 443
  priority                       = 1
  weight                         = 1000
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_origin" "standby" {
  count                          = local.dr ? 1 : 0
  name                           = "standby"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.gateway[0].id
  enabled                        = true
  host_name                      = local.gateway_host["dr"]
  origin_host_header             = local.gateway_host["dr"]
  https_port                     = 443
  priority                       = 2
  weight                         = 1000
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_firewall_policy" "main" {
  count                             = local.pro ? 1 : 0
  name                              = "waf${replace(local.name, "-", "")}"
  resource_group_name               = azurerm_resource_group.main.name
  sku_name                          = azurerm_cdn_frontdoor_profile.main[0].sku_name
  enabled                           = true
  mode                              = var.waf_mode
  request_body_check_enabled        = true
  custom_block_response_status_code = 403
  tags                              = local.tags

  managed_rule {
    type    = "Microsoft_DefaultRuleSet"
    version = "2.1"
    action  = "Block"
  }

  managed_rule {
    type    = "Microsoft_BotManagerRuleSet"
    version = "1.1"
    action  = "Log" # read what it would block (good bots and our own probes) before it blocks
  }

  # A single address cannot hold the API: 600 requests a minute is ten times a busy person. Only the
  # API is counted: a page of the web interface is about thirty small files, and an office behind one
  # address must be able to open it (the gateway limits those per address too).
  custom_rule {
    name                           = "RateLimitPerAddress"
    enabled                        = true
    priority                       = 100
    type                           = "RateLimitRule"
    action                         = "Block"
    rate_limit_duration_in_minutes = 1
    rate_limit_threshold           = 600

    match_condition {
      match_variable     = "RequestUri"
      operator           = "Contains"
      negation_condition = false
      match_values       = ["/v1/"]
    }
  }
}

resource "azurerm_cdn_frontdoor_custom_domain" "main" {
  count                    = local.pro && var.custom_domain != null ? 1 : 0
  name                     = "custom"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main[0].id
  host_name                = var.custom_domain

  tls {
    certificate_type = "ManagedCertificate"
    minimum_version  = "TLS12"
  }
}

resource "azurerm_cdn_frontdoor_route" "main" {
  count                         = local.pro ? 1 : 0
  name                          = "all"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.main[0].id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.gateway[0].id
  cdn_frontdoor_origin_ids = compact([
    azurerm_cdn_frontdoor_origin.primary[0].id,
    try(azurerm_cdn_frontdoor_origin.standby[0].id, null),
  ])
  enabled                = true
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = true
  # Only the public API and the probe: the AI gateway is not reachable through the edge.
  patterns_to_match      = ["/v1/*", "/readyz"]
  supported_protocols    = ["Http", "Https"]
  link_to_default_domain = true

  cdn_frontdoor_custom_domain_ids = var.custom_domain == null ? [] : [azurerm_cdn_frontdoor_custom_domain.main[0].id]

  # An API is not cacheable: nothing here may be stored at the edge.
}

# The web interface: everything that is not the API. The more specific route above wins for /v1 and the
# probe; this one takes the rest and sends it to the gateway's web API under /ui (see gateway.tf).
resource "azurerm_cdn_frontdoor_rule_set" "web" {
  count                    = local.pro && local.web ? 1 : 0
  name                     = "web"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main[0].id
}

resource "azurerm_cdn_frontdoor_rule" "web_prefix" {
  count                     = local.pro && local.web ? 1 : 0
  name                      = "toWebApi"
  cdn_frontdoor_rule_set_id = azurerm_cdn_frontdoor_rule_set.web[0].id
  order                     = 1
  behavior_on_match         = "Continue"

  actions {
    url_rewrite_action {
      source_pattern          = "/"
      destination             = "/ui/"
      preserve_unmatched_path = true
    }
  }
}

resource "azurerm_cdn_frontdoor_route" "web" {
  count                         = local.pro && local.web ? 1 : 0
  name                          = "web"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.main[0].id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.gateway[0].id
  cdn_frontdoor_origin_ids = compact([
    azurerm_cdn_frontdoor_origin.primary[0].id,
    try(azurerm_cdn_frontdoor_origin.standby[0].id, null),
  ])
  cdn_frontdoor_rule_set_ids = [azurerm_cdn_frontdoor_rule_set.web[0].id]
  enabled                    = true
  forwarding_protocol        = "HttpsOnly"
  https_redirect_enabled     = true
  patterns_to_match          = ["/*"]
  supported_protocols        = ["Http", "Https"]
  link_to_default_domain     = true

  cdn_frontdoor_custom_domain_ids = var.custom_domain == null ? [] : [azurerm_cdn_frontdoor_custom_domain.main[0].id]

  # Files only, nothing personal: Front Door keeps what the web container marks as cacheable (the
  # hashed build files, for a year) and passes on what it marks no-cache (the pages and /config.json).
  cache {
    query_string_caching_behavior = "UseQueryString"
    compression_enabled           = true
    content_types_to_compress     = ["text/html", "text/css", "text/javascript", "application/javascript", "application/json", "image/svg+xml", "text/plain"]
  }
}

resource "azurerm_cdn_frontdoor_custom_domain_association" "main" {
  count                          = local.pro && var.custom_domain != null ? 1 : 0
  cdn_frontdoor_custom_domain_id = azurerm_cdn_frontdoor_custom_domain.main[0].id
  cdn_frontdoor_route_ids = compact([
    azurerm_cdn_frontdoor_route.main[0].id,
    try(azurerm_cdn_frontdoor_route.web[0].id, null),
  ])
}

resource "azurerm_cdn_frontdoor_security_policy" "main" {
  count                    = local.pro ? 1 : 0
  name                     = "waf"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main[0].id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.main[0].id

      association {
        patterns_to_match = ["/*"]

        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.main[0].id
        }

        dynamic "domain" {
          for_each = var.custom_domain == null ? [] : [1]
          content {
            cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_custom_domain.main[0].id
          }
        }
      }
    }
  }
}
