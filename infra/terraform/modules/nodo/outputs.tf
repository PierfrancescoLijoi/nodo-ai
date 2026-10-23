output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "api_app_name" {
  value = azurerm_container_app.api["primary"].name
}

output "worker_job_name" {
  value = azurerm_container_app_job.worker["primary"].name
}

output "migrate_job_name" {
  value = azurerm_container_app_job.migrate.name
}

output "retention_job_name" {
  value = azurerm_container_app_job.retention.name
}

output "index_verify_job_name" {
  value = azurerm_container_app_job.index_verify.name
}

output "reindex_job_names" {
  description = "Manual reindex job per region stamp."
  value       = { for k, v in azurerm_container_app_job.reindex : k => v.name }
}

output "sweep_job_name" {
  value = azurerm_container_app_job.sweep.name
}

output "audit_archive_job_name" {
  value = local.pro ? azurerm_container_app_job.audit_archive[0].name : null
}

output "preflight_job_names" {
  description = "Manual first-connection checks, one per region stamp and identity."
  value       = { for k, v in azurerm_container_app_job.preflight : k => v.name }
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "api_url" {
  description = "Base URL the apps call (EXPO_PUBLIC_API_BASE_URL): the edge in the professional tier, the container app in the basic one."
  value       = local.pro ? "https://${local.public_host}" : "https://${azurerm_container_app.api["primary"].ingress[0].fqdn}"
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

output "front_door_id" {
  description = "The value the gateway expects in X-Azure-FDID."
  value       = local.pro ? azurerm_cdn_frontdoor_profile.main[0].resource_guid : null
}

output "identity_client_ids" {
  description = "Client ids of the managed identities, needed to create their database roles."
  value       = { for k, v in azurerm_user_assigned_identity.workload : k => v.client_id if k != "storage" }
}

output "public_host" {
  description = "Host of the public entrance (professional tier): the smoke test and the apps use it."
  value       = local.pro ? local.public_host : null
}

output "api_app_name_dr" {
  description = "Name of the standby API app (professional tier with a standby region)."
  value       = local.dr ? azurerm_container_app.api["dr"].name : null
}

output "resource_group_name_dr" {
  value = local.dr ? azurerm_resource_group.dr[0].name : null
}
