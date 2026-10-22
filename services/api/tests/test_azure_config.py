from __future__ import annotations

import pytest

from nodo_api import admin
from nodo_api.bootstrap import build_adapters
from nodo_api.config import Settings

KEY = "k" * 40
AZURE = {
    "backend": "azure",
    "azure_storage_account": "stnodo",
    "azure_servicebus_fqdn": "sb-nodo.servicebus.windows.net",
    "azure_postgres_host": "psql-nodo.postgres.database.azure.com",
    "azure_postgres_user": "id-nodo-dev-api",
    "azure_search_endpoint": "https://srch-nodo.search.windows.net",
    "azure_openai_endpoint": "https://oai-nodo.openai.azure.com/",
    "azure_speech_endpoint": "https://speech-nodo.cognitiveservices.azure.com/",
}


def _settings(**overrides) -> Settings:
    base = {
        "oidc_issuer": "https://issuer.example/",
        "oidc_audience": "nodo-api",
        "attestation_hmac_key": KEY,
        **AZURE,
    }
    return Settings(**{**base, **overrides})


def test_a_complete_azure_configuration_is_accepted() -> None:
    settings = _settings()
    assert settings.backend == "azure"
    assert settings.azure_servicebus_queue == "ingest"


@pytest.mark.parametrize(
    "missing", ["azure_storage_account", "azure_postgres_user", "azure_speech_endpoint"]
)
def test_every_azure_setting_is_required(missing) -> None:
    with pytest.raises(ValueError, match="azure backend needs"):
        _settings(**{missing: None})


def test_azure_endpoints_must_be_https() -> None:
    with pytest.raises(ValueError, match="https"):
        _settings(azure_search_endpoint="http://srch-nodo.search.windows.net")


def test_a_database_password_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="password"):
        _settings(azure_postgres_password="hunter2")


def test_a_database_password_is_allowed_for_local_development() -> None:
    settings = _settings(environment="test", azure_postgres_password="local-only")
    assert "local-only" not in repr(settings)  # secrets stay out of logs


def test_comma_separated_lists_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("NODO_AZURE_DB_ROLES", "id-nodo-dev-api, id-nodo-dev-worker")
    monkeypatch.setenv("NODO_SPEECH_CANDIDATE_LOCALES", "da-DK,pl-PL")
    settings = _settings()
    assert settings.azure_db_roles == ("id-nodo-dev-api", "id-nodo-dev-worker")
    assert settings.speech_candidate_locales == ("da-DK", "pl-PL")


def test_the_memory_backend_builds_without_any_azure_sdk(monkeypatch) -> None:
    settings = Settings(
        environment="test",
        auth_mode="dev",
        attestation_hmac_key="test-attestation-key-123",
        seed_demo_data=True,
    )
    adapters = build_adapters(settings)
    assert len(adapters.meetings.all_for_workspace("workspace-a")) == 3


def test_admin_rejects_unknown_commands_and_the_memory_backend() -> None:
    memory = Settings(
        environment="test", auth_mode="dev", attestation_hmac_key="test-attestation-key-123"
    )
    with pytest.raises(SystemExit, match="Unknown command"):
        admin.run("explode", memory)
    with pytest.raises(SystemExit, match="NODO_BACKEND=azure"):
        admin.run("migrate", memory)
    with pytest.raises(SystemExit):
        admin.main([])


def test_worker_and_admin_start_without_oidc_settings() -> None:
    base = {k: v for k, v in AZURE.items()}
    for service in ("worker", "admin"):
        settings = Settings(service=service, attestation_hmac_key=KEY, **base)
        assert settings.oidc_issuer is None
    with pytest.raises(ValueError, match="OIDC"):
        Settings(service="api", attestation_hmac_key=KEY, **base)
