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
  type    = string
  default = ""
}

variable "oidc_audience" {
  type    = string
  default = ""
}

variable "api_target_port" {
  description = "API ingress and probe port. Use 80 for the placeholder and 8000 for the NODO image."
  type        = number
  default     = 8000
}

variable "budget_contact_emails" {
  type    = list(string)
  default = []
}

variable "openai_deployments" {
  type = map(object({
    model_name    = string
    model_version = string
    sku_name      = string
    capacity      = number
  }))
  default = {}
}

module "nodo" {
  source = "../../modules/nodo"

  environment           = "dev"
  oidc_issuer           = var.oidc_issuer
  oidc_audience         = var.oidc_audience
  api_target_port       = var.api_target_port
  budget_contact_emails = var.budget_contact_emails
  openai_deployments    = var.openai_deployments
  search_sku            = "free"
  api_min_replicas      = 1
}

output "api_url" {
  value = module.nodo.api_url
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
