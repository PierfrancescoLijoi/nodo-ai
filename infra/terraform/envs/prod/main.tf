terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in a Storage account with Entra-only access. Supply it at init time:
  #   terraform init -backend-config=backend.hcl   (see backend.hcl.example)
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true

  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

variable "subscription_id" {
  description = "Azure subscription that hosts this environment."
  type        = string
}

variable "oidc_issuer" {
  type = string
}

variable "oidc_audience" {
  type = string
}

variable "oidc_allowed_client_ids" {
  type = list(string)
}

variable "custom_domain" {
  type    = string
  default = null
}

variable "operator_cidrs" {
  description = "Addresses of the machines or runners that run Terraform and the deploy pipeline."
  type        = list(string)
}

variable "alert_emails" {
  type = list(string)
}

variable "budget_contact_emails" {
  type = list(string)
}

variable "api_target_port" {
  type    = number
  default = 8000
}

variable "dr_active" {
  type    = bool
  default = false
}

variable "dr_promote_database" {
  type    = bool
  default = false
}

variable "waf_mode" {
  type    = string
  default = "Prevention"
}

variable "openai_deployments" {
  type = map(object({
    model_name    = string
    model_version = string
    sku_name      = string
    capacity      = number
  }))
}

# Launch size: about 1 000 monthly users on docs/CAPACITY-MODEL.md (3 API replicas, one search
# partition). Grow it with the model, not by feel: `python tools/capacity/model.py 5000`.
module "nodo" {
  source = "../../modules/nodo"

  environment = "prod"
  tier        = "professional"
  location    = "swedencentral"
  dr_location = "northeurope"

  oidc_issuer             = var.oidc_issuer
  oidc_audience           = var.oidc_audience
  oidc_allowed_client_ids = var.oidc_allowed_client_ids
  custom_domain           = var.custom_domain
  operator_cidrs          = var.operator_cidrs
  alert_emails            = var.alert_emails
  budget_contact_emails   = var.budget_contact_emails
  api_target_port         = var.api_target_port
  waf_mode                = var.waf_mode
  dr_active               = var.dr_active
  dr_promote_database     = var.dr_promote_database
  openai_deployments      = var.openai_deployments

  api_vcpu          = 1
  api_memory        = "2Gi"
  api_min_replicas  = 3
  api_max_replicas  = 12
  postgres_sku_name = "GP_Standard_D4ds_v5"
  # 128 GiB: twelve months of transcripts are 0.05 TB at 1 000 users; storage grows on demand.
  postgres_storage_mb     = 131072
  postgres_backup_days    = 35
  search_sku              = "standard"
  search_replicas         = 3
  search_partitions       = 1
  audio_retention_days    = 90
  audit_immutable_days    = 365
  lock_resources          = true
  enable_defender         = true
  cost_budget_amount      = 2500
  slo_availability_target = 99.9
  slo_latency_p95_ms      = 800
}

output "api_url" {
  value = module.nodo.api_url
}

output "front_door_id" {
  value = module.nodo.front_door_id
}

output "identity_client_ids" {
  value = module.nodo.identity_client_ids
}

output "container_registry" {
  value = module.nodo.container_registry
}

output "resource_group_name" {
  value = module.nodo.resource_group_name
}

output "acr_name" {
  value = module.nodo.acr_name
}

output "api_app_name" {
  value = module.nodo.api_app_name
}

output "worker_job_name" {
  value = module.nodo.worker_job_name
}

output "migrate_job_name" {
  value = module.nodo.migrate_job_name
}

output "retention_job_name" {
  value = module.nodo.retention_job_name
}

output "index_verify_job_name" {
  value = module.nodo.index_verify_job_name
}

output "reindex_job_names" {
  value = module.nodo.reindex_job_names
}

output "public_host" {
  value = module.nodo.public_host
}

output "api_app_name_dr" {
  value = module.nodo.api_app_name_dr
}

output "resource_group_name_dr" {
  value = module.nodo.resource_group_name_dr
}
