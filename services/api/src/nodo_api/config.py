from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # The worker and the admin job never authenticate end users, so they do not need OIDC settings.
    service: Literal["api", "worker", "admin"] = "api"
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

    # Azure backend (ADR-010). backend=memory keeps the in-memory adapters for tests.
    backend: Literal["memory", "azure"] = "memory"
    azure_storage_account: str | None = None
    azure_servicebus_fqdn: str | None = None
    azure_servicebus_queue: str = "ingest"
    azure_postgres_host: str | None = None
    azure_postgres_database: str = "nodo"
    azure_postgres_user: str | None = None
    azure_postgres_password: SecretStr | None = Field(default=None, repr=False)
    azure_db_roles: Annotated[tuple[str, ...], NoDecode] = ()
    azure_search_endpoint: str | None = None
    search_index_name: str = "segments"
    azure_openai_endpoint: str | None = None
    openai_chat_deployment: str = "chat"
    openai_embedding_deployment: str = "embedding"
    openai_embedding_dimensions: int = Field(default=1536, ge=8, le=4096)
    openai_api_version: str = "2024-10-21"
    azure_speech_endpoint: str | None = None
    speech_candidate_locales: Annotated[tuple[str, ...], NoDecode] = (
        "en-GB",
        "pl-PL",
        "it-IT",
        "da-DK",
    )
    upload_session_seconds: int = Field(default=900, ge=60, le=3600)

    @field_validator("azure_db_roles", "speech_candidate_locales", mode="before")
    @classmethod
    def split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def validate_environment(self) -> Settings:
        development = self.environment in NON_PRODUCTION
        if self.auth_mode == "dev" and not development:
            raise ValueError("Dev auth requires NODO_ENVIRONMENT to be dev or test")
        if self.seed_demo_data and not development:
            raise ValueError("Demo data requires NODO_ENVIRONMENT to be dev or test")
        if self.auth_mode == "oidc" and self.service == "api":
            self._validate_oidc()
        if self.backend == "azure":
            self._validate_azure(development)
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

    def _validate_azure(self, development: bool) -> None:
        required = {
            "NODO_AZURE_STORAGE_ACCOUNT": self.azure_storage_account,
            "NODO_AZURE_SERVICEBUS_FQDN": self.azure_servicebus_fqdn,
            "NODO_AZURE_POSTGRES_HOST": self.azure_postgres_host,
            "NODO_AZURE_POSTGRES_USER": self.azure_postgres_user,
            "NODO_AZURE_SEARCH_ENDPOINT": self.azure_search_endpoint,
            "NODO_AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
            "NODO_AZURE_SPEECH_ENDPOINT": self.azure_speech_endpoint,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"The azure backend needs: {', '.join(missing)}")
        if self.azure_postgres_password is not None and not development:
            raise ValueError("A database password is only allowed in dev and test; use Entra auth")
        for endpoint in (
            self.azure_search_endpoint,
            self.azure_openai_endpoint,
            self.azure_speech_endpoint,
        ):
            if endpoint and not endpoint.startswith("https://"):
                raise ValueError("Azure endpoints must use https")
