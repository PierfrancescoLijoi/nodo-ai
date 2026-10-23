# Disaster recovery (ADR-017): a second region holds a copy of the data and a stopped copy of the
# compute. Targets, set by the design and exercised by the runbook: RPO of seconds for the database
# and of minutes for audio; RTO of 30 to 60 minutes, most of it the decision and the promotion.
#
#   data     PostgreSQL cross-region replica; storage RA-GZRS; Service Bus geo-DR alias; registry geo-replica
#   derived  search index rebuilt from the replica (ADR-007); model and speech accounts are stateless
#   compute  environment and API in the standby network, scaled to zero until var.dr_active is set

resource "azurerm_servicebus_namespace" "dr" {
  count                         = local.dr ? 1 : 0
  name                          = "sb-${local.short_name}-dr"
  location                      = var.dr_location
  resource_group_name           = azurerm_resource_group.dr[0].name
  sku                           = "Premium"
  capacity                      = 1
  premium_messaging_partitions  = 1
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false
  tags                          = local.tags
}

resource "azurerm_servicebus_namespace_disaster_recovery_config" "main" {
  count                = local.dr ? 1 : 0
  name                 = "sbdr-${local.short_name}"
  primary_namespace_id = azurerm_servicebus_namespace.main.id
  partner_namespace_id = azurerm_servicebus_namespace.dr[0].id

  # Pairing fails if one namespace has a private endpoint and the other has none, and adding an
  # endpoint to a paired namespace fails: every endpoint exists before the pairing does.
  depends_on = [azurerm_private_endpoint.main]
}

# Cross-region read replica. Promoting it (var.dr_promote_database) is the point of no return of a
# failover: the replica becomes a normal server and the old primary can no longer follow it.
resource "azurerm_postgresql_flexible_server" "replica" {
  count                         = local.dr ? 1 : 0
  name                          = "psql-${local.short_name}-dr"
  location                      = var.dr_location
  resource_group_name           = azurerm_resource_group.dr[0].name
  version                       = "16"
  sku_name                      = var.postgres_sku_name
  storage_mb                    = var.postgres_storage_mb
  auto_grow_enabled             = true
  create_mode                   = "Replica"
  source_server_id              = azurerm_postgresql_flexible_server.main.id
  replication_role              = var.dr_promote_database ? "None" : null
  public_network_access_enabled = false
  delegated_subnet_id           = azurerm_subnet.postgres["dr"].id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres["dr"].id
  zone                          = "1"
  tags                          = local.tags

  lifecycle {
    ignore_changes = [zone]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

# After a promotion the replica becomes a normal server: it needs the same Entra administrator
# that bootstraps the schema. (HA and the maintenance window are added to it by a later apply.)
resource "azurerm_postgresql_flexible_server_active_directory_administrator" "replica_migrate" {
  count               = local.dr && var.dr_promote_database ? 1 : 0
  server_name         = azurerm_postgresql_flexible_server.replica[0].name
  resource_group_name = azurerm_resource_group.dr[0].name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = azurerm_user_assigned_identity.workload["migrate"].principal_id
  principal_name      = azurerm_user_assigned_identity.workload["migrate"].name
  principal_type      = "ServicePrincipal"
}

# Search in the standby region: sized for a rebuild, resized by the failover runbook.
resource "azurerm_search_service" "dr" {
  count                         = local.dr ? 1 : 0
  name                          = "srch-${local.short_name}-dr"
  location                      = var.dr_location
  resource_group_name           = azurerm_resource_group.dr[0].name
  sku                           = var.search_sku
  replica_count                 = 1
  partition_count               = var.search_partitions
  local_authentication_enabled  = false
  public_network_access_enabled = false
  tags                          = local.tags
}

resource "azurerm_cognitive_account" "speech_dr" {
  count                         = local.dr ? 1 : 0
  name                          = "speech-${local.short_name}-dr"
  location                      = var.dr_location
  resource_group_name           = azurerm_resource_group.dr[0].name
  kind                          = "SpeechServices"
  sku_name                      = "S0"
  custom_subdomain_name         = "speech-${local.short_name}-dr"
  local_auth_enabled            = false
  public_network_access_enabled = false
  tags                          = local.tags
}

resource "azurerm_cognitive_account" "openai_dr" {
  count                         = local.dr ? 1 : 0
  name                          = "oai-${local.short_name}-dr"
  location                      = var.dr_location
  resource_group_name           = azurerm_resource_group.dr[0].name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "oai-${local.short_name}-dr"
  local_auth_enabled            = false
  public_network_access_enabled = false
  tags                          = local.tags
}

resource "azurerm_cognitive_deployment" "models_dr" {
  for_each             = local.dr ? var.openai_deployments : {}
  name                 = each.key
  cognitive_account_id = azurerm_cognitive_account.openai_dr[0].id

  model {
    format  = "OpenAI"
    name    = each.value.model_name
    version = each.value.model_version
  }

  sku {
    name     = each.value.sku_name
    capacity = each.value.capacity
  }
}
