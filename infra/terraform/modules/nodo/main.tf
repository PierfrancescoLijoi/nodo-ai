data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 4
  upper   = false
  special = false
}

locals {
  name       = "${var.project}-${var.environment}"
  short_name = "${var.project}${var.environment}${random_string.suffix.result}"
  pro        = var.tier == "professional"
  dr         = local.pro && var.dr_location != null
  tenant_id  = var.tenant_id != "" ? var.tenant_id : data.azurerm_client_config.current.tenant_id

  # Region "stamps": everything regional is built once per entry (the primary, and the standby when
  # disaster recovery is on). Network, private endpoints and DNS links iterate over this map.
  regions = merge(
    local.pro ? { primary = { location = var.location, cidr = var.vnet_cidr } } : {},
    local.dr ? { dr = { location = var.dr_location, cidr = var.dr_vnet_cidr } } : {},
  )

  tags = merge(var.tags, {
    project     = var.project
    environment = var.environment
    tier        = var.tier
    managed_by  = "terraform"
  })
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# The standby region has its own group: its network, private DNS zones and compute live there.
resource "azurerm_resource_group" "dr" {
  count    = local.dr ? 1 : 0
  name     = "rg-${local.name}-dr"
  location = var.dr_location
  tags     = local.tags
}

locals {
  rg_names = {
    primary = azurerm_resource_group.main.name
    dr      = try(azurerm_resource_group.dr[0].name, null)
  }
}

# --- observability ----------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

# --- identities -------------------------------------------------------------------------------

locals {
  identity_names = concat(["api", "worker", "migrate", "retention", "maintenance", "audit", "storage"], local.web ? ["web"] : [])
}

resource "azurerm_user_assigned_identity" "workload" {
  for_each            = toset(local.identity_names)
  name                = "id-${local.name}-${each.key}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# --- registry ---------------------------------------------------------------------------------

resource "azurerm_container_registry" "main" {
  name                          = "cr${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = local.pro ? "Premium" : "Basic"
  admin_enabled                 = false
  zone_redundancy_enabled       = local.pro
  retention_policy_in_days      = local.pro ? 30 : null
  public_network_access_enabled = !local.pro || length(var.operator_cidrs) > 0
  tags                          = local.tags

  network_rule_set = local.pro ? [{
    default_action = "Deny"
    ip_rule        = [for cidr in var.operator_cidrs : { action = "Allow", ip_range = cidr }]
  }] : null

  lifecycle {
    # The deploy pipeline opens the firewall to its runner for the length of a job and closes it again.
    ignore_changes = [network_rule_set]
  }

  dynamic "georeplications" {
    for_each = local.dr ? [var.dr_location] : []
    content {
      location                = georeplications.value
      zone_redundancy_enabled = true
      tags                    = local.tags
    }
  }
}

# --- key vault --------------------------------------------------------------------------------

resource "azurerm_key_vault" "main" {
  name                          = "kv-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = local.pro ? "premium" : "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = local.pro ? 90 : 7
  purge_protection_enabled      = local.pro
  public_network_access_enabled = !local.pro || length(var.operator_cidrs) > 0
  tags                          = local.tags

  dynamic "network_acls" {
    for_each = local.pro ? [1] : []
    content {
      default_action = "Deny"
      bypass         = "AzureServices"
      ip_rules       = var.operator_cidrs
    }
  }
}

# The principal running Terraform writes the generated secrets and keys.
resource "azurerm_role_assignment" "terraform_kv_officer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "random_password" "attestation_key" {
  length  = 48
  special = false
}

resource "azurerm_key_vault_secret" "attestation_key" {
  name         = "attestation-hmac-key"
  value        = random_password.attestation_key.result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_officer]
}

# Salt of the pseudonymised ids in telemetry (ADR-018): the same person is the same token across
# logs, and nobody can map a token back to a person without this value.
resource "random_password" "telemetry_salt" {
  length  = 48
  special = false
}

resource "azurerm_key_vault_secret" "telemetry_salt" {
  name         = "telemetry-salt"
  value        = random_password.telemetry_salt.result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_officer]
}

# Customer-managed key for the storage account that holds audio (professional tier). Rotated by
# Key Vault every 90 days; the storage account follows the new version on its own.
resource "azurerm_key_vault_key" "storage" {
  count        = local.pro ? 1 : 0
  name         = "storage-cmk"
  key_vault_id = azurerm_key_vault.main.id
  key_type     = "RSA-HSM"
  key_size     = 3072
  key_opts     = ["wrapKey", "unwrapKey"]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }
    expire_after         = "P180D"
    notify_before_expiry = "P29D"
  }

  depends_on = [azurerm_role_assignment.terraform_kv_officer]
}

resource "azurerm_role_assignment" "storage_cmk" {
  count                = local.pro ? 1 : 0
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.workload["storage"].principal_id
  principal_type       = "ServicePrincipal"
}

# --- storage ----------------------------------------------------------------------------------

# The audio account keeps a public endpoint on purpose: apps upload straight to it with a
# write-only user-delegation SAS, and the speech service reads with a read-only one. No shared
# key exists, SAS lifetime is capped, and the workloads reach it over a private endpoint
# (ADR-016 explains why this is the only public data endpoint).
resource "azurerm_storage_account" "main" {
  name                              = "st${local.short_name}"
  location                          = azurerm_resource_group.main.location
  resource_group_name               = azurerm_resource_group.main.name
  account_tier                      = "Standard"
  account_replication_type          = local.pro ? "GZRS" : "LRS"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = local.pro
  tags                              = local.tags

  blob_properties {
    # No versioning and no change feed on the audio account: a version of an erased recording
    # would keep it alive. Soft delete is the only copy left after an erasure, for 7 days (ADR-019).
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }

    # The web app uploads audio from the browser straight to the blob, by short-lived SAS link. The
    # browser only does that if the account says this page may (CORS): its own origin, PUT only.
    dynamic "cors_rule" {
      for_each = local.web ? [1] : []
      content {
        allowed_origins    = [local.web_origin]
        allowed_methods    = ["PUT", "OPTIONS"]
        allowed_headers    = ["x-ms-*", "content-type"]
        exposed_headers    = ["x-ms-request-id", "etag"]
        max_age_in_seconds = 3600
      }
    }
  }

  dynamic "sas_policy" {
    for_each = local.pro ? [1] : []
    content {
      expiration_period = "00.06:00:00"
      expiration_action = "Log"
    }
  }

  dynamic "identity" {
    for_each = local.pro ? [1] : []
    content {
      type         = "UserAssigned"
      identity_ids = [azurerm_user_assigned_identity.workload["storage"].id]
    }
  }

  dynamic "customer_managed_key" {
    for_each = local.pro ? [1] : []
    content {
      key_vault_key_id          = azurerm_key_vault_key.storage[0].versionless_id
      user_assigned_identity_id = azurerm_user_assigned_identity.workload["storage"].id
    }
  }

  depends_on = [azurerm_role_assignment.storage_cmk]
}

resource "azurerm_storage_container" "audio" {
  name                  = "audio"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "exports" {
  name                  = "exports"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

# The platform removes data on schedule whatever the application did (ARCHITECTURE.md section 9).
# The application deletes audio earlier, per meeting, according to the policy the user attested to;
# this rule is the backstop at the longest policy.
resource "azurerm_storage_management_policy" "retention" {
  storage_account_id = azurerm_storage_account.main.id

  rule {
    name    = "delete-audio-after-retention"
    enabled = true

    filters {
      prefix_match = ["audio/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.audio_retention_days
      }
    }
  }

  rule {
    name    = "delete-exports-after-a-week"
    enabled = true

    filters {
      prefix_match = ["exports/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = 7
      }
    }
  }
}

# Audit evidence: written once, readable by compliance, impossible to alter or delete before the
# retention period ends (WORM). Private only.
resource "azurerm_storage_account" "audit" {
  count                             = local.pro ? 1 : 0
  name                              = "sta${local.short_name}"
  location                          = azurerm_resource_group.main.location
  resource_group_name               = azurerm_resource_group.main.name
  account_tier                      = "Standard"
  account_replication_type          = "GZRS"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = true
  public_network_access_enabled     = false
  tags                              = local.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "audit" {
  count                 = local.pro ? 1 : 0
  name                  = "audit"
  storage_account_id    = azurerm_storage_account.audit[0].id
  container_access_type = "private"
}

resource "azurerm_storage_container_immutability_policy" "audit" {
  count                                 = local.pro ? 1 : 0
  storage_container_resource_manager_id = azurerm_storage_container.audit[0].id
  immutability_period_in_days           = var.audit_immutable_days
  protected_append_writes_all_enabled   = true
  locked                                = var.audit_policy_locked
}

# --- service bus ------------------------------------------------------------------------------

resource "azurerm_servicebus_namespace" "main" {
  name                          = "sb-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = local.pro ? "Premium" : "Standard"
  capacity                      = local.pro ? 1 : 0
  premium_messaging_partitions  = local.pro ? 1 : 0
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = !local.pro
  tags                          = local.tags
}

resource "azurerm_servicebus_queue" "ingest" {
  name         = "ingest"
  namespace_id = azurerm_servicebus_namespace.main.id

  max_delivery_count                      = 5
  lock_duration                           = "PT5M"
  default_message_ttl                     = "P2D"
  requires_duplicate_detection            = true
  duplicate_detection_history_time_window = "PT1H"
  dead_lettering_on_message_expiration    = true
}

# --- postgres ---------------------------------------------------------------------------------

resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "azurerm_key_vault_secret" "postgres_admin" {
  name         = "postgres-admin-password"
  value        = random_password.postgres_admin.result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_officer]
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  sku_name                      = var.postgres_sku_name
  storage_mb                    = var.postgres_storage_mb
  auto_grow_enabled             = local.pro
  backup_retention_days         = var.postgres_backup_days
  geo_redundant_backup_enabled  = local.dr
  administrator_login           = "nodoadmin"
  administrator_password        = random_password.postgres_admin.result
  public_network_access_enabled = !local.pro
  delegated_subnet_id           = local.pro ? azurerm_subnet.postgres["primary"].id : null
  private_dns_zone_id           = local.pro ? azurerm_private_dns_zone.postgres["primary"].id : null
  zone                          = local.pro ? "1" : null
  tags                          = local.tags

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true # break-glass only; the password lives in Key Vault
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  dynamic "high_availability" {
    for_each = local.pro ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }

  dynamic "maintenance_window" {
    for_each = local.pro ? [1] : []
    content {
      day_of_week  = 0 # Sunday, 03:00 UTC: the quietest hour for a European user base
      start_hour   = 3
      start_minute = 0
    }
  }

  lifecycle {
    ignore_changes = [zone, high_availability[0].standby_availability_zone]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "nodo" {
  name      = "nodo"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR,PG_TRGM,PGCRYPTO"
}

# Query-level evidence for audits and slow-query work; the rest of the diagnostics are in monitoring.tf.
resource "azurerm_postgresql_flexible_server_configuration" "track_io" {
  count     = local.pro ? 1 : 0
  name      = "track_io_timing"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_min_duration" {
  count     = local.pro ? 1 : 0
  name      = "log_min_duration_statement"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "1000"
}

# Basic tier: reachable from Azure services only, authenticated with Entra and TLS.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  count            = local.pro ? 0 : 1
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "migrate" {
  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = azurerm_user_assigned_identity.workload["migrate"].principal_id
  principal_name      = azurerm_user_assigned_identity.workload["migrate"].name
  principal_type      = "ServicePrincipal"
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "human" {
  count               = var.postgres_entra_admin == null ? 0 : 1
  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = var.postgres_entra_admin.object_id
  principal_name      = var.postgres_entra_admin.principal_name
  principal_type      = var.postgres_entra_admin.principal_type
}

# --- search -----------------------------------------------------------------------------------

# The index is derived from PostgreSQL (ADR-007): losing it is a rebuild, not a data loss, so the
# standby region builds its own from the replica and nothing is copied across.
resource "azurerm_search_service" "main" {
  name                          = "srch-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = var.search_sku
  replica_count                 = local.pro ? var.search_replicas : 1
  partition_count               = local.pro ? var.search_partitions : 1
  local_authentication_enabled  = var.search_sku == "free"
  public_network_access_enabled = !local.pro
  semantic_search_sku           = var.search_sku == "free" ? null : "standard"
  tags                          = local.tags
}

# --- AI services (Speech and OpenAI) ----------------------------------------------------------

resource "azurerm_cognitive_account" "speech" {
  name                          = "speech-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  kind                          = "SpeechServices"
  sku_name                      = "S0"
  custom_subdomain_name         = "speech-${local.short_name}"
  local_auth_enabled            = false
  public_network_access_enabled = !local.pro
  tags                          = local.tags
}

resource "azurerm_cognitive_account" "openai" {
  name                          = "oai-${local.short_name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "oai-${local.short_name}"
  local_auth_enabled            = false
  public_network_access_enabled = !local.pro
  tags                          = local.tags
}

resource "azurerm_cognitive_deployment" "models" {
  for_each             = var.openai_deployments
  name                 = each.key
  cognitive_account_id = azurerm_cognitive_account.openai.id

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

# --- budget -----------------------------------------------------------------------------------

resource "azurerm_consumption_budget_resource_group" "main" {
  count             = length(var.budget_contact_emails) == 0 ? 0 : 1
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.cost_budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", timestamp())
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThan"
      threshold_type = "Actual"
      contact_emails = var.budget_contact_emails
    }
  }

  # A forecast alert catches the overrun before it happens.
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.budget_contact_emails
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
