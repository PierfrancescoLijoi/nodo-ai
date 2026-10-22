output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "api_url" {
  description = "Base URL the mobile app calls (EXPO_PUBLIC_API_BASE_URL)."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "container_registry" {
  value = azurerm_container_registry.main.login_server
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.main.name}.search.windows.net"
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "speech_endpoint" {
  value = azurerm_cognitive_account.speech.endpoint
}

output "identity_client_ids" {
  description = "Client ids of the managed identities, needed to create their database roles."
  value = {
    api    = azurerm_user_assigned_identity.api.client_id
    worker = azurerm_user_assigned_identity.worker.client_id
  }
}
