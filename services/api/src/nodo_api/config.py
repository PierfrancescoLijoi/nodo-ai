from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NODO_", extra="ignore")

    auth_mode: Literal["dev", "oidc"] = "dev"
    dev_token: str = Field(default="dev-only-change-me", repr=False)
    dev_user_id: str = "dev-user"
    dev_workspace_id: str = "dev-workspace"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    attestation_hmac_key: str = Field(default="dev-only-attestation-key", repr=False)
    graph_node_limit: int = Field(default=50, ge=1, le=500)
    cors_origins: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_auth_settings(self) -> Settings:
        if self.auth_mode == "oidc" and (not self.oidc_issuer or not self.oidc_audience):
            raise ValueError("OIDC issuer and audience are required in oidc mode")
        if self.auth_mode == "oidc" and self.attestation_hmac_key == "dev-only-attestation-key":
            raise ValueError("A non-development attestation HMAC key is required in oidc mode")
        return self
