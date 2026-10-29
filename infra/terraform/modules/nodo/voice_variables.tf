# Inputs of the real-time voice gateway (voice.tf, ADR-028). Everything is off by default: with
# voice_enabled = false the module plans exactly what it planned before.

variable "voice_enabled" {
  description = "Deploy the real-time voice gateway: a Redis cache for session state, a second Container App per stamp from the API image with NODO_VOICE_ENABLED, and (professional tier) an API Management WebSocket API. Off by default."
  type        = bool
  default     = false
}

variable "voice_min_replicas" {
  description = "Floor of voice replicas per stamp. Two at least: a rolling update drains one replica while the other takes its sessions over."
  type        = number
  default     = 2

  validation {
    condition     = var.voice_min_replicas >= 2
    error_message = "voice_min_replicas must be at least 2: a drain needs another replica to hand sessions to."
  }
}

variable "voice_max_replicas" {
  type    = number
  default = 10

  validation {
    condition     = var.voice_max_replicas >= var.voice_min_replicas
    error_message = "voice_max_replicas must not be below voice_min_replicas."
  }
}

variable "voice_sessions_per_replica" {
  description = "NODO_VOICE_MAX_SESSIONS_PER_REPLICA, and the base of the connection scale rule (the app scales out at 80 percent of it). 25 is the design hypothesis of VOICE-REALTIME-ARCHITECTURE.md until a load test on this SKU says otherwise."
  type        = number
  default     = 25

  validation {
    condition     = var.voice_sessions_per_replica >= 5 && var.voice_sessions_per_replica <= 500
    error_message = "voice_sessions_per_replica must be between 5 and 500."
  }
}

variable "voice_drain_seconds" {
  description = "NODO_VOICE_DRAIN_SECONDS: how long a stopping replica waits for its sessions to be handed over. The termination grace period is derived from it."
  type        = number
  default     = 10

  validation {
    condition     = var.voice_drain_seconds >= 1 && var.voice_drain_seconds <= 120
    error_message = "voice_drain_seconds must be between 1 and 120 (the application refuses more)."
  }
}

variable "voice_vcpu" {
  description = "vCPU per voice replica. One process runs per replica (the session hub lives in the process), so more than one vCPU only helps the speech SDK threads."
  type        = number
  default     = 1
}

variable "voice_memory" {
  type    = string
  default = "2Gi"
}

variable "voice_redis_capacity" {
  description = "Redis size: C<n> in the basic tier (Standard), P<n> in the professional one (Premium, zone redundant). Sessions hold text and counters for hours at most; 1 is plenty until the load test says otherwise."
  type        = number
  default     = 1
}

variable "voice_connects_per_minute" {
  description = "New WebSocket handshakes per minute per client address at the gateway. The ticket is checked by the application, so this is the only limit in front of it."
  type        = number
  default     = 300
}
