# Least privilege per identity (AZURE-ARCHITECTURE.md section 3). No keys, no connection strings.

locals {
  identities = {
    api     = azurerm_user_assigned_identity.api.principal_id
    worker  = azurerm_user_assigned_identity.worker.principal_id
    migrate = azurerm_user_assigned_identity.migrate.principal_id
  }

  role_assignments = {
    api_acr_pull      = { principal = "api", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    worker_acr_pull   = { principal = "worker", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    api_kv_secrets    = { principal = "api", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    worker_kv_secrets = { principal = "worker", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    api_blob          = { principal = "api", scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
    worker_blob       = { principal = "worker", scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
    api_sb_send       = { principal = "api", scope = azurerm_servicebus_namespace.main.id, role = "Azure Service Bus Data Sender" }
    worker_sb_receive = { principal = "worker", scope = azurerm_servicebus_namespace.main.id, role = "Azure Service Bus Data Receiver" }
    # The API also re-indexes on transcript correction and removes documents on erasure.
    api_search_write    = { principal = "api", scope = azurerm_search_service.main.id, role = "Search Index Data Contributor" }
    worker_search_write = { principal = "worker", scope = azurerm_search_service.main.id, role = "Search Index Data Contributor" }
    api_openai          = { principal = "api", scope = azurerm_cognitive_account.openai.id, role = "Cognitive Services OpenAI User" }
    worker_openai       = { principal = "worker", scope = azurerm_cognitive_account.openai.id, role = "Cognitive Services OpenAI User" }
    worker_speech       = { principal = "worker", scope = azurerm_cognitive_account.speech.id, role = "Cognitive Services Speech User" }
    migrate_acr_pull    = { principal = "migrate", scope = azurerm_container_registry.main.id, role = "AcrPull" }
    migrate_kv_secrets  = { principal = "migrate", scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
    migrate_search      = { principal = "migrate", scope = azurerm_search_service.main.id, role = "Search Service Contributor" }
  }
}

resource "azurerm_role_assignment" "identity" {
  for_each             = local.role_assignments
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = local.identities[each.value.principal]
  principal_type       = "ServicePrincipal"
}
