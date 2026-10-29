# Governance and protection (ADR-019). Guardrails are code here: where data may live, what the
# security benchmark says about it, which resources cannot be deleted by accident, and malware
# scanning on what people upload.

data "azurerm_subscription" "current" {}

locals {
  allowed_locations = length(var.allowed_locations) > 0 ? var.allowed_locations : compact([var.location, var.dr_location])

  # Built-in definitions, referenced by their published ids.
  policy_allowed_locations = "/providers/Microsoft.Authorization/policyDefinitions/e56962a6-4747-49cd-b67b-bf8b01975c4c"
  policy_mcsb              = "/providers/Microsoft.Authorization/policySetDefinitions/1f3afdf9-d0c9-4c3d-847f-89da613e70a8"
}

# Data residency: nothing is created outside the agreed regions ("global" is for Front Door and DNS).
resource "azurerm_resource_group_policy_assignment" "locations" {
  count                = local.pro ? 1 : 0
  name                 = "allowed-locations"
  resource_group_id    = azurerm_resource_group.main.id
  policy_definition_id = local.policy_allowed_locations
  display_name         = "Resources only in the agreed regions"
  parameters = jsonencode({
    listOfAllowedLocations = { value = concat(local.allowed_locations, ["global"]) }
  })
}

resource "azurerm_resource_group_policy_assignment" "locations_dr" {
  count                = local.dr ? 1 : 0
  name                 = "allowed-locations-dr"
  resource_group_id    = azurerm_resource_group.dr[0].id
  policy_definition_id = local.policy_allowed_locations
  display_name         = "Resources only in the agreed regions"
  parameters = jsonencode({
    listOfAllowedLocations = { value = concat(local.allowed_locations, ["global"]) }
  })
}

# The Microsoft cloud security benchmark, in audit mode: a report on drift, not a way to break a deploy.
resource "azurerm_resource_group_policy_assignment" "benchmark" {
  count                = local.pro ? 1 : 0
  name                 = "security-benchmark"
  resource_group_id    = azurerm_resource_group.main.id
  policy_definition_id = local.policy_mcsb
  display_name         = "Microsoft cloud security benchmark"
  enforce              = false
}

# --- Defender for Cloud, subscription-wide: switched on deliberately ----------------------------

resource "azurerm_security_center_subscription_pricing" "plan" {
  for_each      = var.enable_defender ? toset(["StorageAccounts", "KeyVaults", "Arm", "OpenSourceRelationalDatabases"]) : toset([])
  resource_type = each.key
  tier          = "Standard"
  subplan       = each.key == "StorageAccounts" ? "DefenderForStorageV2" : null

  dynamic "extension" {
    for_each = each.key == "StorageAccounts" ? ["OnUploadMalwareScanning", "SensitiveDataDiscovery"] : []
    content {
      name = extension.value
      additional_extension_properties = extension.value == "OnUploadMalwareScanning" ? {
        CapGBPerMonthPerStorageAccount = "5000"
      } : null
    }
  }
}

# Who changed what in the subscription, kept with the rest of the evidence.
resource "azurerm_monitor_diagnostic_setting" "activity_log" {
  count                      = var.enable_defender ? 1 : 0
  name                       = "activity-to-workspace"
  target_resource_id         = data.azurerm_subscription.current.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "Administrative"
  }
  enabled_log {
    category = "Security"
  }
  enabled_log {
    category = "Policy"
  }
  enabled_log {
    category = "Alert"
  }
}

# --- protection against accidents -------------------------------------------------------------

locals {
  locked = var.lock_resources ? merge(
    {
      postgres = azurerm_postgresql_flexible_server.main.id
      keyvault = azurerm_key_vault.main.id
      audio    = azurerm_storage_account.main.id
      queue    = azurerm_servicebus_namespace.main.id
    },
    local.pro ? { audit = azurerm_storage_account.audit[0].id } : {},
  ) : {}
}

resource "azurerm_management_lock" "data" {
  for_each   = local.locked
  name       = "no-delete-${each.key}"
  scope      = each.value
  lock_level = "CanNotDelete"
  notes      = "Data service of ${local.name}. Remove the lock in a reviewed change before deleting."
}
