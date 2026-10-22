"""Builds the adapter set for a backend. The API, the worker and the admin commands all start
here, so what runs in Azure and what the tests run differ only in these functions (ADR-006)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from nodo_api.config import Settings
from nodo_api.services.ingestion import IngestionService

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass(slots=True)
class Adapters:
    meetings: Any
    transcripts: Any
    blobs: Any
    jobs: Any
    retriever: Any
    graph: Any
    llm: Any
    audit: Any
    speech: Any
    extractor: Any
    runtime: Any = None  # the local backend's moving parts (worker thread, model server)


@dataclass(slots=True)
class WorkerRuntime:
    ingestion: IngestionService
    receiver_factory: Callable[[int], AbstractContextManager[Any]]


def memory_adapters(settings: Settings) -> Adapters:
    from nodo_api.adapters.memory import (
        MemoryAuditLog,
        MemoryBlobStore,
        MemoryGraphStore,
        MemoryJobQueue,
        MemoryMeetingRepository,
        MemorySpeech,
        MemoryTranscriptRepository,
        MockLlm,
        MockRetriever,
        seed_memory_adapters,
    )
    from nodo_api.adapters.memory.extractor import KeywordExtractor

    meetings = MemoryMeetingRepository()
    transcripts = MemoryTranscriptRepository()
    retriever = MockRetriever()
    graph = MemoryGraphStore()
    if settings.seed_demo_data:
        seed_memory_adapters(meetings, transcripts, retriever, graph)
    return Adapters(
        meetings,
        transcripts,
        MemoryBlobStore(),
        MemoryJobQueue(),
        retriever,
        graph,
        MockLlm(),
        MemoryAuditLog(),
        MemorySpeech(),
        KeywordExtractor(),
    )


def azure_clients(settings: Settings) -> dict[str, Any]:
    """The Azure SDK clients, all authenticated with the app's managed identity."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.servicebus import ServiceBusClient
    from azure.storage.blob import BlobServiceClient
    from openai import AzureOpenAI

    credential = DefaultAzureCredential()
    return {
        "credential": credential,
        "blob": BlobServiceClient(
            f"https://{settings.azure_storage_account}.blob.core.windows.net", credential=credential
        ),
        "search": SearchClient(
            settings.azure_search_endpoint, settings.search_index_name, credential=credential
        ),
        "search_index": SearchIndexClient(settings.azure_search_endpoint, credential=credential),
        "servicebus": ServiceBusClient(settings.azure_servicebus_fqdn, credential=credential),
        "openai": AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=get_bearer_token_provider(credential, COGNITIVE_SCOPE),
            api_version=settings.openai_api_version,
        ),
        "speech_token": lambda: credential.get_token(COGNITIVE_SCOPE).token,
    }


def postgres_database(settings: Settings, credential: Any = None):
    from nodo_api.adapters.azure.postgres import PostgresDatabase

    if settings.azure_postgres_password is not None:  # dev and test only (validated in Settings)
        password = settings.azure_postgres_password.get_secret_value()
        dsn = (
            f"host={settings.azure_postgres_host} dbname={settings.azure_postgres_database} "
            f"user={settings.azure_postgres_user} password={password}"
        )
        return PostgresDatabase.from_dsn(dsn)
    return PostgresDatabase.from_entra(
        settings.azure_postgres_host,
        settings.azure_postgres_database,
        settings.azure_postgres_user,
        credential,
    )


def azure_adapters(settings: Settings, clients: dict[str, Any] | None = None) -> Adapters:
    from nodo_api.adapters.azure.blob import AzureBlobStore
    from nodo_api.adapters.azure.graph_pg import PgGraphStore
    from nodo_api.adapters.azure.openai_extractor import AzureOpenAiExtractor
    from nodo_api.adapters.azure.openai_llm import AzureOpenAiEmbedder, AzureOpenAiLlm
    from nodo_api.adapters.azure.postgres import (
        PgAuditLog,
        PgJobOutbox,
        PgMeetingRepository,
        PgTranscriptRepository,
    )
    from nodo_api.adapters.azure.search import AzureSearchRetriever
    from nodo_api.adapters.azure.servicebus import ServiceBusJobQueue
    from nodo_api.adapters.azure.speech import AzureSpeechToText

    c = clients or azure_clients(settings)
    db = postgres_database(settings, c["credential"])
    embedder = AzureOpenAiEmbedder(
        c["openai"], settings.openai_embedding_deployment, settings.openai_embedding_dimensions
    )
    return Adapters(
        meetings=PgMeetingRepository(db),
        transcripts=PgTranscriptRepository(db),
        blobs=AzureBlobStore(
            settings.azure_storage_account,
            c["blob"],
            upload_seconds=settings.upload_session_seconds,
        ),
        jobs=ServiceBusJobQueue(
            PgJobOutbox(db),
            lambda: c["servicebus"].get_queue_sender(settings.azure_servicebus_queue),
        ),
        retriever=AzureSearchRetriever(c["search"], embedder),
        graph=PgGraphStore(db),
        llm=AzureOpenAiLlm(c["openai"], settings.openai_chat_deployment),
        audit=PgAuditLog(db),
        speech=AzureSpeechToText(settings.azure_speech_endpoint, c["speech_token"]),
        extractor=AzureOpenAiExtractor(c["openai"], settings.openai_chat_deployment),
    )


def build_adapters(settings: Settings) -> Adapters:
    if settings.backend == "azure":
        return azure_adapters(settings)
    if settings.backend == "local":
        from nodo_api.adapters.local.bootstrap import local_adapters

        return local_adapters(settings)
    return memory_adapters(settings)


def build_worker_runtime(settings: Settings) -> WorkerRuntime:
    clients = azure_clients(settings)
    adapters = azure_adapters(settings, clients)
    ingestion = IngestionService(
        adapters.meetings,
        adapters.transcripts,
        adapters.blobs,
        adapters.retriever,
        adapters.speech,
        settings.speech_candidate_locales,
        adapters.extractor,
        adapters.graph,
    )
    return WorkerRuntime(
        ingestion,
        lambda idle: clients["servicebus"].get_queue_receiver(
            settings.azure_servicebus_queue, max_wait_time=idle
        ),
    )
