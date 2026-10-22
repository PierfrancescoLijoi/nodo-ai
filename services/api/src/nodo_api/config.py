from __future__ import annotations

from pathlib import Path
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
    auth_mode: Literal["dev", "oidc", "local"] = "oidc"
    dev_token: str = Field(default="dev-only-change-me", min_length=16, repr=False)
    dev_user_id: str = "dev-user"
    dev_workspace_id: str = "dev-workspace"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_allowed_client_ids: Annotated[tuple[str, ...], NoDecode] = ()
    oidc_max_token_age_seconds: int = Field(default=3600, ge=60, le=86400)
    attestation_hmac_key: str = Field(default=DEV_ATTESTATION_KEY, min_length=16, repr=False)
    known_notice_versions: tuple[str, ...] = ("2026-10-v1",)
    known_retention_policies: tuple[str, ...] = ("retention-7d", "retention-30d", "retention-90d")
    seed_demo_data: bool = False
    graph_node_limit: int = Field(default=50, ge=1, le=500)
    cors_origins: tuple[str, ...] = ()

    # Azure backend (ADR-010). backend=memory keeps the in-memory adapters for tests.
    backend: Literal["memory", "azure", "local"] = "memory"
    # Local backend (ADR-014): everything on this computer. auth_mode=local accepts one secret
    # token, generated at every start by the desktop shell, and the server listens on loopback.
    local_token: SecretStr | None = Field(default=None, repr=False)
    local_data_dir: Path | None = None
    local_models_dir: Path | None = None
    local_web_dir: Path | None = None  # the exported web app, served by the engine itself
    local_public_url: str = "http://127.0.0.1:8765"
    local_llm_context: int = Field(default=16384, ge=2048, le=131072)
    local_extract_window_tokens: int = Field(default=3500, ge=500, le=100000)
    local_warm_up: bool = False  # load the search model at start instead of at the first use
    local_search_margin: float = Field(default=0.035, ge=0.0, le=0.5)

    azure_storage_account: str | None = None
    azure_servicebus_fqdn: str | None = None
    azure_servicebus_queue: str = "ingest"
    azure_postgres_host: str | None = None
    azure_postgres_database: str = "nodo"
    azure_postgres_user: str | None = None
    azure_postgres_password: SecretStr | None = Field(default=None, repr=False)
    azure_db_roles: Annotated[tuple[str, ...], NoDecode] = ()
    azure_retention_roles: Annotated[tuple[str, ...], NoDecode] = ()
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

    # Failure containment (ADR-017). Timeouts are per call; the bulkheads are concurrent calls per
    # replica, sized from the capacity model (docs/CAPACITY-MODEL.md).
    openai_timeout_seconds: float = Field(default=30.0, ge=1, le=600)
    openai_extraction_timeout_seconds: float = Field(default=240.0, ge=10, le=900)
    bulkhead_llm: int = Field(default=16, ge=1, le=256)
    bulkhead_extraction: int = Field(default=4, ge=1, le=64)
    bulkhead_embedding: int = Field(default=16, ge=1, le=256)
    bulkhead_search: int = Field(default=32, ge=1, le=256)
    bulkhead_speech: int = Field(default=8, ge=1, le=64)
    bulkhead_servicebus: int = Field(default=16, ge=1, le=256)
    bulkhead_storage: int = Field(default=32, ge=1, le=256)
    azure_postgres_pool_max: int = Field(default=10, ge=1, le=100)
    azure_postgres_pool_timeout_seconds: float = Field(default=5.0, ge=0.5, le=60)
    azure_postgres_statement_timeout_ms: int = Field(default=15_000, ge=1000, le=600_000)
    telemetry_sampling_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    max_in_flight_requests: int = Field(default=200, ge=10, le=5000)
    max_concurrent_queries: int = Field(default=16, ge=1, le=256)

    @field_validator(
        "azure_db_roles",
        "azure_retention_roles",
        "speech_candidate_locales",
        "oidc_allowed_client_ids",
        mode="before",
    )
    @classmethod
    def split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def validate_environment(self) -> Settings:
        development = self.environment in NON_PRODUCTION
        if self.auth_mode == "local":
            self._validate_local()
        elif self.backend == "local":
            raise ValueError("The local backend requires auth_mode=local")
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

    def _validate_local(self) -> None:
        if self.backend != "local":
            raise ValueError("auth_mode=local is only for the local backend")
        if (
            self.local_token is None
            or len(self.local_token.get_secret_value()) < MIN_PRODUCTION_KEY_LENGTH
        ):
            raise ValueError(
                f"NODO_LOCAL_TOKEN must be at least {MIN_PRODUCTION_KEY_LENGTH} characters"
            )
        if not self.local_public_url.startswith("http://127.0.0.1:"):
            raise ValueError("The local engine is only reachable on 127.0.0.1")

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
