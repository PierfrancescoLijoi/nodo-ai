# Professional tier: one virtual network per region ("stamp"), every data service behind a private
# endpoint, and private DNS zones per region so each stamp resolves its own endpoints.
# Address plan per /16: 0.0/23 container apps, 2.0/24 private endpoints, 3.0/24 postgres, 4.0/24 gateway.

locals {
  subnet_cidrs = {
    for r, v in local.regions : r => {
      aca      = cidrsubnet(v.cidr, 7, 0)
      endpoint = cidrsubnet(v.cidr, 8, 2)
      postgres = cidrsubnet(v.cidr, 8, 3)
      apim     = cidrsubnet(v.cidr, 8, 4)
    }
  }
}

resource "azurerm_virtual_network" "main" {
  for_each            = local.regions
  name                = "vnet-${local.name}-${each.key}"
  location            = each.value.location
  resource_group_name = local.rg_names[each.key]
  address_space       = [each.value.cidr]
  tags                = local.tags
}

resource "azurerm_subnet" "aca" {
  for_each             = local.regions
  name                 = "snet-aca"
  resource_group_name  = local.rg_names[each.key]
  virtual_network_name = azurerm_virtual_network.main[each.key].name
  address_prefixes     = [local.subnet_cidrs[each.key].aca]

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "endpoint" {
  for_each                          = local.regions
  name                              = "snet-endpoints"
  resource_group_name               = local.rg_names[each.key]
  virtual_network_name              = azurerm_virtual_network.main[each.key].name
  address_prefixes                  = [local.subnet_cidrs[each.key].endpoint]
  private_endpoint_network_policies = "Enabled"
}

resource "azurerm_subnet" "postgres" {
  for_each             = local.regions
  name                 = "snet-postgres"
  resource_group_name  = local.rg_names[each.key]
  virtual_network_name = azurerm_virtual_network.main[each.key].name
  address_prefixes     = [local.subnet_cidrs[each.key].postgres]

  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "apim" {
  for_each             = local.regions
  name                 = "snet-gateway"
  resource_group_name  = local.rg_names[each.key]
  virtual_network_name = azurerm_virtual_network.main[each.key].name
  address_prefixes     = [local.subnet_cidrs[each.key].apim]
}

# --- gateway subnet rules ---------------------------------------------------------------------
# Only Front Door (and callers inside the network) reach the API gateway; Azure's own management
# plane reaches it for the operations the service needs.

resource "azurerm_network_security_group" "apim" {
  for_each            = local.regions
  name                = "nsg-${local.name}-gateway-${each.key}"
  location            = each.value.location
  resource_group_name = local.rg_names[each.key]
  tags                = local.tags

  dynamic "security_rule" {
    for_each = {
      front-door      = { priority = 100, port = "443", source = "AzureFrontDoor.Backend" }
      inside-network  = { priority = 110, port = "443", source = "VirtualNetwork" }
      apim-management = { priority = 120, port = "3443", source = "ApiManagement" }
      load-balancer   = { priority = 130, port = "6390", source = "AzureLoadBalancer" }
    }
    content {
      name                       = security_rule.key
      priority                   = security_rule.value.priority
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = security_rule.value.port
      source_address_prefix      = security_rule.value.source
      destination_address_prefix = "VirtualNetwork"
    }
  }

  dynamic "security_rule" {
    for_each = {
      storage = { priority = 100, port = "443", destination = "Storage" }
      entra   = { priority = 110, port = "443", destination = "AzureActiveDirectory" }
      vault   = { priority = 120, port = "443", destination = "AzureKeyVault" }
      monitor = { priority = 130, port = "443", destination = "AzureMonitor" }
    }
    content {
      name                       = "out-${security_rule.key}"
      priority                   = security_rule.value.priority
      direction                  = "Outbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = security_rule.value.port
      source_address_prefix      = "VirtualNetwork"
      destination_address_prefix = security_rule.value.destination
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "apim" {
  for_each                  = local.regions
  subnet_id                 = azurerm_subnet.apim[each.key].id
  network_security_group_id = azurerm_network_security_group.apim[each.key].id
}

# --- private DNS ------------------------------------------------------------------------------

locals {
  dns_zones = [
    "privatelink.vaultcore.azure.net",
    "privatelink.azurecr.io",
    "privatelink.blob.core.windows.net",
    "privatelink.servicebus.windows.net",
    "privatelink.search.windows.net",
    "privatelink.openai.azure.com",
    "privatelink.cognitiveservices.azure.com",
  ]

  dns_zone_instances = {
    for pair in setproduct(keys(local.regions), local.dns_zones) :
    "${pair[0]}/${pair[1]}" => { region = pair[0], zone = pair[1] }
  }
}

resource "azurerm_private_dns_zone" "endpoint" {
  for_each            = local.dns_zone_instances
  name                = each.value.zone
  resource_group_name = local.rg_names[each.value.region]
  tags                = local.tags
}

# The standby region has its own resource group: Azure refuses two zones of one name in a group.
resource "azurerm_private_dns_zone_virtual_network_link" "endpoint" {
  for_each              = local.dns_zone_instances
  name                  = "link-${each.value.region}"
  resource_group_name   = local.rg_names[each.value.region]
  private_dns_zone_name = azurerm_private_dns_zone.endpoint[each.key].name
  virtual_network_id    = azurerm_virtual_network.main[each.value.region].id
  tags                  = local.tags
}

resource "azurerm_private_dns_zone" "postgres" {
  for_each            = local.regions
  name                = "${local.name}-${each.key}.private.postgres.database.azure.com"
  resource_group_name = local.rg_names[each.key]
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  for_each              = local.regions
  name                  = "link-${each.key}"
  resource_group_name   = local.rg_names[each.key]
  private_dns_zone_name = azurerm_private_dns_zone.postgres[each.key].name
  virtual_network_id    = azurerm_virtual_network.main[each.key].id
  tags                  = local.tags
}

# --- private endpoints ------------------------------------------------------------------------

locals {
  # Services that exist once (and are reached from every stamp) carry a single resource id; services
  # that exist once per region carry a map of ids by stamp.
  pe_targets = {
    keyvault = { sub = "vault", zone = "privatelink.vaultcore.azure.net", id = azurerm_key_vault.main.id }
    registry = { sub = "registry", zone = "privatelink.azurecr.io", id = azurerm_container_registry.main.id }
    audio    = { sub = "blob", zone = "privatelink.blob.core.windows.net", id = azurerm_storage_account.main.id }
    audit    = { sub = "blob", zone = "privatelink.blob.core.windows.net", id = try(azurerm_storage_account.audit[0].id, null) }
    servicebus = {
      sub  = "namespace"
      zone = "privatelink.servicebus.windows.net"
      ids  = { primary = azurerm_servicebus_namespace.main.id, dr = try(azurerm_servicebus_namespace.dr[0].id, null) }
    }
    search = {
      sub  = "searchService"
      zone = "privatelink.search.windows.net"
      ids  = { primary = azurerm_search_service.main.id, dr = try(azurerm_search_service.dr[0].id, null) }
    }
    openai = {
      sub  = "account"
      zone = "privatelink.openai.azure.com"
      ids  = { primary = azurerm_cognitive_account.openai.id, dr = try(azurerm_cognitive_account.openai_dr[0].id, null) }
    }
    speech = {
      sub  = "account"
      zone = "privatelink.cognitiveservices.azure.com"
      ids  = { primary = azurerm_cognitive_account.speech.id, dr = try(azurerm_cognitive_account.speech_dr[0].id, null) }
    }
  }

  # One endpoint per service per stamp. Existence depends only on the stamps, never on resource
  # attributes, so the keys are known at plan time.
  private_endpoints = {
    for pair in setproduct(keys(local.regions), keys(local.pe_targets)) :
    "${pair[0]}/${pair[1]}" => {
      region      = pair[0]
      name        = pair[1]
      sub         = local.pe_targets[pair[1]].sub
      zone        = local.pe_targets[pair[1]].zone
      resource_id = try(local.pe_targets[pair[1]].ids[pair[0]], try(local.pe_targets[pair[1]].id, null))
    }
  }
}

resource "azurerm_private_endpoint" "main" {
  for_each            = local.private_endpoints
  name                = "pe-${local.name}-${each.value.region}-${each.value.name}"
  location            = local.regions[each.value.region].location
  resource_group_name = local.rg_names[each.value.region]
  subnet_id           = azurerm_subnet.endpoint[each.value.region].id
  tags                = local.tags

  private_service_connection {
    name                           = each.value.name
    private_connection_resource_id = each.value.resource_id
    subresource_names              = [each.value.sub]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.endpoint["${each.value.region}/${each.value.zone}"].id]
  }
}
