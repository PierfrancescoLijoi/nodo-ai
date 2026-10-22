variable "project" {
  description = "Short project name used in resource names."
  type        = string
  default     = "nodo"
}

variable "environment" {
  description = "Deployment environment. This is the Azure environment, not the API NODO_ENVIRONMENT (which is always prod in Azure so dev conveniences stay off)."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "location" {
  description = "Azure region. Confirm that every service and model is available there before the first apply."
  type        = string
  default     = "swedencentral"
}

variable "tags" {
  type    = map(string)
  default = {}
}

# --- application ------------------------------------------------------------------------------

variable "api_image" {
  description = "Container image for the API and migration job. The default placeholder listens on port 80, so api_target_port must be set to 80 until the first real image is deployed."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "api_target_port" {
  description = "Port the API container listens on. Keep this at 80 for the placeholder image and use the 8000 default once the first real image is deployed."
  type        = number
  default     = 8000
}

variable "api_min_replicas" {
  type    = number
  default = 1
}

variable "api_max_replicas" {
  type    = number
  default = 5
}

variable "worker_max_executions" {
  description = "Upper bound of parallel worker executions per polling interval."
  type        = number
  default     = 5
}

variable "oidc_issuer" {
  description = "Issuer URL of the Entra External ID tenant (https). Leave empty until the tenant exists; the API refuses to start without it."
  type        = string
  default     = ""
}

variable "oidc_audience" {
  description = "Audience (application id) the API accepts."
  type        = string
  default     = ""
}

# --- data services ----------------------------------------------------------------------------

variable "postgres_sku_name" {
  type    = string
  default = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  type    = number
  default = 32768
}

variable "postgres_backup_days" {
  type    = number
  default = 7
}

variable "postgres_entra_admin" {
  description = "Entra principal that becomes the database administrator, used to create least-privilege roles for the identities. Null skips it."
  type = object({
    object_id      = string
    principal_name = string
    principal_type = string
  })
  default = null
}

variable "search_sku" {
  description = "free, basic or standard."
  type        = string
  default     = "free"

  validation {
    condition     = contains(["free", "basic", "standard"], var.search_sku)
    error_message = "search_sku must be free, basic or standard."
  }
}

variable "audio_retention_days" {
  description = "Default retention of raw audio. A lifecycle rule deletes audio blobs after this many days."
  type        = number
  default     = 30
}

variable "openai_deployments" {
  description = "Model deployments to create in the OpenAI account, keyed by deployment name. Empty by default: choose models only after checking regional availability."
  type = map(object({
    model_name    = string
    model_version = string
    sku_name      = string
    capacity      = number
  }))
  default = {}
}

variable "openai_chat_deployment" {
  description = "Azure OpenAI chat deployment name. This should match a key in openai_deployments."
  type        = string
  default     = "chat"
}

variable "openai_embedding_deployment" {
  description = "Azure OpenAI embedding deployment name. This should match a key in openai_deployments."
  type        = string
  default     = "embedding"
}

variable "embedding_dimensions" {
  description = "Vector dimensions produced by the configured embedding deployment."
  type        = number
  default     = 1536
}

# --- cost -------------------------------------------------------------------------------------

variable "budget_amount" {
  description = "Monthly budget for the resource group, in the billing currency."
  type        = number
  default     = 100
}

variable "budget_contact_emails" {
  description = "Who is notified at 50, 80 and 100 percent. Empty disables the budget."
  type        = list(string)
  default     = []
}
