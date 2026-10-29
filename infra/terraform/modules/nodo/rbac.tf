# Least privilege per identity (AZURE-ARCHITECTURE.md section 3). No keys, no connection strings.
# The same grant covers the standby region: its services are listed next to the primary's.

locals {
  principal = { for k, v in azurerm_user_assigned_identity.workload : k => v.principal_id }

  # Services that exist once per region, by stamp.
  regional = {
    servicebus = merge({ primary = azurerm_servicebus_namespace.main.id }, local.dr ? { dr = azurerm_servicebus_namespace.dr[0].id } : {})
    search     = merge({ primary = azurerm_search_service.main.id }, local.dr ? { dr = azurerm_search_service.dr[0].id } : {})
    openai     = merge({ primary = azurerm_cognitive_account.openai.id }, local.dr ? { dr = azurerm_cognitive_account.openai_dr[0].id } : {})
    speech     = merge({ primary = azurerm_cognitive_account.speech.id }, local.dr ? { dr = azurerm_cognitive_account.speech_dr[0].id } : {})
  }

  # Grants on services that exist once: who, on what, which role.
  single_grants = {
    api_acr_pull       = { principal = "api", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    worker_acr_pull    = { principal = "worker", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    migrate_acr_pull   = { principal = "migrate", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    retention_acr_pull = { principal = "retention", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    maintenance_acr    = { principal = "maintenance", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    api_kv_secrets     = { principal = "api", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    worker_kv_secrets  = { principal = "worker", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    migrate_kv_secrets = { principal = "migrate", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    retention_kv       = { principal = "retention", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    maintenance_kv     = { principal = "maintenance", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    api_blob           = { principal = "api", scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
    worker_blob        = { principal = "worker", scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
    # The retention job lists and deletes audio. The role also allows reading: Azure has no role that
    # lists without reading, so the job is trusted not to (its code path never downloads).
    retention_blob = { principal = "retention", scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
  }

  # Grants repeated for every stamp: principal, service, role.
  regional_grants = {
    api_sb_send = { principal = "api", service = "servicebus", role = "Azure Service Bus Data Sender" }
    # Index maintenance asks for a re-analysis through the queue, re-embeds text for the index
    # and writes to it; it reads no audio and no speech results.
    maintenance_sb_send = { principal = "maintenance", service = "servicebus", role = "Azure Service Bus Data Sender" }
    maintenance_search  = { principal = "maintenance", service = "search", role = "Search Index Data Contributor" }
    maintenance_openai  = { principal = "maintenance", service = "openai", role = "Cognitive Services OpenAI User" }
    worker_sb_receive   = { principal = "worker", service = "servicebus", role = "Azure Service Bus Data Receiver" }
    # The API also re-indexes on transcript correction and removes documents on erasure.
    api_search_write    = { principal = "api", service = "search", role = "Search Index Data Contributor" }
    worker_search_write = { principal = "worker", service = "search", role = "Search Index Data Contributor" }
    migrate_search      = { principal = "migrate", service = "search", role = "Search Service Contributor" }
    # The models are reached through the gateway in the professional tier; direct grants stay for
    # the basic tier and as the gateway's own fallback path.
    api_openai    = { principal = "api", service = "openai", role = "Cognitive Services OpenAI User" }
    worker_openai = { principal = "worker", service = "openai", role = "Cognitive Services OpenAI User" }
    worker_speech = { principal = "worker", service = "speech", role = "Cognitive Services Speech User" }
  }

  regional_role_assignments = merge([
    for name, grant in local.regional_grants : {
      for stamp, scope in local.regional[grant.service] :
      "${name}_${stamp}" => { principal = grant.principal, scope = scope, role = grant.role }
    }
  ]...)

  # The audit archive writes into the private account with the retention policy, and nowhere else.
  audit_grants = local.pro ? {
    audit_acr_pull = { principal = "audit", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    audit_kv       = { principal = "audit", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    audit_blob     = { principal = "audit", scope = azurerm_storage_account.audit[0].id, role = "Storage Blob Data Contributor" }
  } : {}

  # The web container only pulls its image.
  web_grants = local.web ? {
    web_acr_pull = { principal = "web", scope = azurerm_container_registry.main.id, role = "AcrPull" }
  } : {}

  role_assignments = merge(local.single_grants, local.audit_grants, local.web_grants, local.regional_role_assignments)
}

resource "azurerm_role_assignment" "identity" {
  for_each             = local.role_assignments
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = local.principal[each.value.principal]
  principal_type       = "ServicePrincipal"
}
