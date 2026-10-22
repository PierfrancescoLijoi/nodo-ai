from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_ATTESTATION_KEY = "dev-only-attestation-key"
MIN_PRODUCTION_KEY_LENGTH = 32
NON_PRODUCTION = frozenset({"dev", "test"})


class Settings(BaseSettings):
    """Fail closed: with no environment variables the service refuses to start.

    Development conveniences (dev tokens, demo data, the default HMAC key) require an explicit
    NODO_ENVIRONMENT of dev or test.
    """

    model_config = SettingsConfigDict(env_prefix="NODO_", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "prod"
    auth_mode: Literal["dev", "oidc"] = "oidc"
    dev_token: str = Field(default="dev-only-change-me", min_length=16, repr=False)
    dev_user_id: str = "dev-user"
    dev_workspace_id: str = "dev-workspace"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_max_token_age_seconds: int = Field(default=3600, ge=60, le=86400)
    attestation_hmac_key: str = Field(default=DEV_ATTESTATION_KEY, min_length=16, repr=False)
    known_notice_versions: tuple[str, ...] = ("2026-10-v1",)
    known_retention_policies: tuple[str, ...] = ("retention-7d", "retention-30d", "retention-90d")
    seed_demo_data: bool = False
    graph_node_limit: int = Field(default=50, ge=1, le=500)
    cors_origins: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_environment(self) -> Settings:
        development = self.environment in NON_PRODUCTION
        if self.auth_mode == "dev" and not development:
            raise ValueError("Dev auth requires NODO_ENVIRONMENT to be dev or test")
        if self.seed_demo_data and not development:
            raise ValueError("Demo data requires NODO_ENVIRONMENT to be dev or test")
        if self.auth_mode == "oidc":
            self._validate_oidc()
        if not development and (
            self.attestation_hmac_key == DEV_ATTESTATION_KEY
            or len(self.attestation_hmac_key) < MIN_PRODUCTION_KEY_LENGTH
        ):
            raise ValueError(
                f"A random attestation HMAC key of at least {MIN_PRODUCTION_KEY_LENGTH} "
                "characters is required outside dev and test"
            )
        return self

    def _validate_oidc(self) -> None:
        if not self.oidc_issuer or not self.oidc_audience:
            raise ValueError("OIDC issuer and audience are required in oidc mode")
        for url in (self.oidc_issuer, self.oidc_jwks_url):
            if url and not url.startswith("https://"):
                raise ValueError("OIDC URLs must use https")
