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

variable "tier" {
  description = <<-EOT
    basic: one region, public endpoints guarded by Entra, cheapest SKUs (a personal or demo deployment).
    professional: private network, edge and API gateway, zone redundancy, a disaster recovery region,
    full observability and compliance controls (docs/AZURE-ARCHITECTURE.md). It costs real money.
  EOT
  type        = string
  default     = "basic"

  validation {
    condition     = contains(["basic", "professional"], var.tier)
    error_message = "tier must be basic or professional."
  }
}

variable "location" {
  description = "Primary Azure region. It needs availability zones and every model in openai_deployments."
  type        = string
  default     = "swedencentral"
}

variable "dr_location" {
  description = "Secondary region for the professional tier (data replicated, compute on standby). Null disables disaster recovery."
  type        = string
  default     = null
}

variable "dr_active" {
  description = "The failover lever. False: the secondary region keeps data only (compute scaled to zero). True: it serves traffic. Flip it in the same change that promotes the Postgres replica (RUNBOOKS.md)."
  type        = bool
  default     = false
}

variable "dr_promote_database" {
  description = "Promotes the standby PostgreSQL replica to a standalone server. Irreversible: set it only during a real failover or a rehearsal on a throwaway environment."
  type        = bool
  default     = false
}

variable "allowed_locations" {
  description = "Regions Azure Policy lets resources be created in (EU residency). Empty means the primary and secondary regions only."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

# --- network ----------------------------------------------------------------------------------

variable "vnet_cidr" {
  description = "Address space of the primary virtual network (professional tier)."
  type        = string
  default     = "10.40.0.0/16"
}

variable "dr_vnet_cidr" {
  description = "Address space of the secondary virtual network. Must not overlap vnet_cidr."
  type        = string
  default     = "10.41.0.0/16"
}

variable "operator_cidrs" {
  description = "Public addresses allowed through the data-service firewalls: the machine or runner that runs Terraform and the deploy pipeline. Everything else reaches data services over private endpoints."
  type        = list(string)
  default     = []
}

# --- edge and gateway -------------------------------------------------------------------------

variable "custom_domain" {
  description = "Public host name served by Front Door (api.example.com). Null uses the generated azurefd.net name."
  type        = string
  default     = null
}

variable "waf_mode" {
  description = "Prevention blocks, Detection only logs. Start in Detection on a new domain, move to Prevention after reading the logs."
  type        = string
  default     = "Prevention"

  validation {
    condition     = contains(["Prevention", "Detection"], var.waf_mode)
    error_message = "waf_mode must be Prevention or Detection."
  }
}

variable "apim_sku_name" {
  description = "API Management SKU. Premium_N gives availability zones and an SLA; Developer_1 is for staging and has no SLA."
  type        = string
  default     = "Premium_1"
}

variable "apim_publisher_email" {
  description = "Contact shown in API Management."
  type        = string
  default     = "platform@example.invalid"
}

variable "rate_limit_calls_per_minute" {
  description = "Requests per minute per signed-in user at the gateway. The API sheds load on its own as well."
  type        = number
  default     = 300
}

variable "ai_tokens_per_minute" {
  description = "Model tokens per minute per calling identity at the AI gateway (llm-token-limit)."
  type        = number
  default     = 400000
}

# --- application ------------------------------------------------------------------------------

variable "api_image" {
  description = "Container image for the API and jobs. The default placeholder listens on port 80, so api_target_port must be set to 80 until the first real image is deployed."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "api_target_port" {
  description = "Port the API container listens on. 80 for the placeholder image, 8000 for the real one."
  type        = number
  default     = 8000
}

variable "api_min_replicas" {
  description = "Floor of API replicas. The professional tier never goes below three (one per availability zone)."
  type        = number
  default     = 1
}

variable "api_max_replicas" {
  type    = number
  default = 5
}

variable "api_vcpu" {
  description = "vCPU per API replica. WEB_CONCURRENCY follows it (one process per vCPU)."
  type        = number
  default     = 0.5
}

variable "api_memory" {
  type    = string
  default = "1Gi"
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

variable "oidc_allowed_client_ids" {
  description = "Application ids of the clients allowed to call the API (the azp claim). Empty accepts any client of the tenant."
  type        = list(string)
  default     = []
}

variable "tenant_id" {
  description = "Entra tenant that issues the tokens the gateway validates. Empty uses the tenant of the identity running Terraform."
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

variable "search_replicas" {
  description = "Search replicas. Three give the read and write SLA; the capacity model sets the real number."
  type        = number
  default     = 3
}

variable "search_partitions" {
  description = "Search partitions; the index must fit (docs/CAPACITY-MODEL.md)."
  type        = number
  default     = 1
}

variable "audio_retention_days" {
  description = "Backstop for raw audio: a lifecycle rule deletes audio blobs after this many days whatever the application did. Set it to the longest per-meeting retention policy (90 days)."
  type        = number
  default     = 90
}

variable "audit_immutable_days" {
  description = "How long the audit archive cannot be changed or deleted by anyone (WORM)."
  type        = number
  default     = 365
}

variable "audit_policy_locked" {
  description = "True makes the audit retention policy irreversible. Leave it false until the retention period has been agreed with compliance."
  type        = bool
  default     = false
}

variable "openai_deployments" {
  description = "Model deployments, keyed by deployment name. Empty by default: choose models only after checking regional availability. The professional tier creates the same set in the secondary region."
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

# --- operations -------------------------------------------------------------------------------

variable "log_retention_days" {
  description = "Retention of operational logs in Log Analytics. Audit evidence is archived separately."
  type        = number
  default     = 90
}

variable "slo_availability_target" {
  description = "Availability objective of the API over 30 days, in percent. It drives the burn-rate alerts."
  type        = number
  default     = 99.9
}

variable "slo_latency_p95_ms" {
  description = "Objective for the 95th percentile of read requests, in milliseconds."
  type        = number
  default     = 800
}

variable "alert_emails" {
  description = "Who is paged. Empty creates the alerts without a receiver."
  type        = list(string)
  default     = []
}

variable "lock_resources" {
  description = "Put CanNotDelete locks on the data services."
  type        = bool
  default     = false
}

variable "enable_defender" {
  description = "Turn on Defender for Cloud plans (storage with malware scanning, Key Vault, Resource Manager, containers registry, databases). Subscription-wide."
  type        = bool
  default     = false
}

variable "cost_budget_amount" {
  description = "Monthly budget for the resource group, in the billing currency."
  type        = number
  default     = 100
}

variable "budget_contact_emails" {
  description = "Who is notified at 50, 80 and 100 percent. Empty disables the budget."
  type        = list(string)
  default     = []
}
