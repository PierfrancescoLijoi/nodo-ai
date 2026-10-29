"""Settings of the voice gateway. Off by default; every value is bounded."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nodo_api.voice.store import Limits
from nodo_api.voice.tickets import MIN_KEY_BYTES


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NODO_VOICE_", extra="ignore")

    enabled: bool = False
    ticket_key: SecretStr | None = Field(default=None, repr=False)
    ticket_ttl_seconds: int = Field(default=60, ge=10, le=300)
    # Capacity. The per-replica value is the first design hypothesis of the architecture note
    # (25 live sessions per replica) and is meant to be replaced by what the load test shows.
    max_sessions_per_replica: int = Field(default=25, ge=1, le=500)
    max_sessions_total: int = Field(default=500, ge=1, le=100_000)
    max_sessions_per_workspace: int = Field(default=5, ge=1, le=100)
    # An opened session holds a slot until it lapses, even if nobody connects.
    max_sessions_per_user: int = Field(default=3, ge=1, le=100)
    max_pending_hello: int = Field(default=100, ge=1, le=10_000)  # sockets not yet authenticated
    heartbeat_seconds: int = Field(default=15, ge=5, le=60)
    reconnect_grace_seconds: int = Field(default=30, ge=5, le=300)
    max_session_seconds: int = Field(default=240 * 60, ge=60, le=5 * 3600)
    replay_events: int = Field(default=256, ge=16, le=4096)
    send_queue: int = Field(default=256, ge=16, le=4096)
    audio_burst_seconds: int = Field(default=30, ge=5, le=120)  # replay after a reconnect
    drain_seconds: int = Field(default=10, ge=1, le=120)
    redis_url: SecretStr | None = Field(default=None, repr=False)
    redis_max_connections: int = Field(default=64, ge=4, le=1000)
    recognizer: Literal["azure", "local"] = "azure"
    synthesizer: Literal["azure", "none"] = "azure"

    @property
    def limits(self) -> Limits:
        return Limits(
            self.max_sessions_total, self.max_sessions_per_workspace, self.max_sessions_per_user
        )

    @property
    def lease_ms(self) -> int:
        return int(self.heartbeat_seconds * 2.5 * 1000)

    @property
    def admission_ttl_seconds(self) -> int:
        return self.heartbeat_seconds * 3 + self.reconnect_grace_seconds

    @model_validator(mode="after")
    def _required_when_enabled(self) -> VoiceSettings:
        if not self.enabled:
            return self
        if self.ticket_key is None or len(self.ticket_key.get_secret_value()) < MIN_KEY_BYTES:
            raise ValueError("NODO_VOICE_TICKET_KEY must be at least 32 characters")
        return self
