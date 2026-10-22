"""The Azure clients and adapters build from settings alone: no network, no Azure account. This
catches wrong SDK argument names and missing wiring before a deployment does."""

from __future__ import annotations

import pytest

pytest.importorskip("azure.identity")

from nodo_api import bootstrap
from nodo_api.adapters.azure.blob import AzureBlobStore
from nodo_api.adapters.azure.openai_llm import AzureOpenAiLlm
from nodo_api.adapters.azure.postgres import PgMeetingRepository
from nodo_api.adapters.azure.search import AzureSearchRetriever
from nodo_api.adapters.azure.servicebus import ServiceBusJobQueue
from nodo_api.adapters.azure.speech import AzureSpeechToText
from nodo_api.config import Settings

SETTINGS = Settings(
    oidc_issuer="https://issuer.example/",
    oidc_audience="nodo-api",
    attestation_hmac_key="k" * 40,
    backend="azure",
    azure_storage_account="stnodo",
    azure_servicebus_fqdn="sb-nodo.servicebus.windows.net",
    azure_postgres_host="psql-nodo.postgres.database.azure.com",
    azure_postgres_user="id-nodo-dev-api",
    azure_search_endpoint="https://srch-nodo.search.windows.net",
    azure_openai_endpoint="https://oai-nodo.openai.azure.com/",
    azure_speech_endpoint="https://speech-nodo.cognitiveservices.azure.com/",
)


def test_sdk_clients_construct_from_settings() -> None:
    clients = bootstrap.azure_clients(SETTINGS)
    assert {"credential", "blob", "search", "search_index", "servicebus", "openai"} <= set(clients)
    assert clients["blob"].account_name == "stnodo"
    assert callable(clients["speech_token"])


def test_every_port_gets_an_azure_adapter(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "postgres_database", lambda settings, credential=None: object())
    adapters = bootstrap.build_adapters(SETTINGS)
    assert isinstance(adapters.meetings, PgMeetingRepository)
    assert isinstance(adapters.blobs, AzureBlobStore)
    assert isinstance(adapters.jobs, ServiceBusJobQueue)
    assert isinstance(adapters.retriever, AzureSearchRetriever)
    assert isinstance(adapters.llm, AzureOpenAiLlm)
    assert isinstance(adapters.speech, AzureSpeechToText)


def test_the_worker_runtime_opens_a_receiver_on_the_ingest_queue(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "postgres_database", lambda settings, credential=None: object())
    runtime = bootstrap.build_worker_runtime(SETTINGS)
    receiver = runtime.receiver_factory(30)
    assert receiver is not None
