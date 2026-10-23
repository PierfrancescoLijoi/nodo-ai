"""Builds the adapter set for a backend. The API, the worker and the admin commands all start
here, so what runs in Azure and what the tests run differ only in these functions (ADR-006)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from nodo_api.config import Settings
from nodo_api.resilience import Guarded, RetryPolicy, make_guard
from nodo_api.services.exports import ExportService
from nodo_api.services.ingestion import IngestionService

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
# Interactive calls give up quickly (a person is waiting); batch calls can wait out a bad minute.
INTERACTIVE_RETRY = RetryPolicy(attempts=2, base_seconds=0.2, max_seconds=1.0, deadline_seconds=12)
BATCH_RETRY = RetryPolicy(attempts=4, base_seconds=2.0, max_seconds=30.0, deadline_seconds=600)


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
    readiness: tuple[tuple[str, Callable[[], None]], ...] = ()  # (name, check) for /readyz
    retention: Any = None  # the audio-retention store, for the sweep job
    exports: Any = None  # export requests (ADR-019)
    speech_jobs: Any = None  # the speech job a meeting waits for (staged transcription)


@dataclass(slots=True)
class WorkerRuntime:
    ingestion: IngestionService
    receiver_factory: Callable[[int], AbstractContextManager[Any]]
    exports: Any = None


def memory_adapters(settings: Settings) -> Adapters:
    from nodo_api.adapters.memory import (
        MemoryAuditLog,
        MemoryBlobStore,
        MemoryExportStore,
        MemoryGraphStore,
        MemoryJobQueue,
        MemoryMeetingRepository,
        MemoryRetentionStore,
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
        retention=MemoryRetentionStore(meetings),
        exports=MemoryExportStore(),
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
            # One layer retries (Guard); the SDK's own retries would multiply ours.
            max_retries=0,
            timeout=(
                settings.openai_extraction_timeout_seconds
                if settings.service == "worker"
                else settings.openai_timeout_seconds
            ),
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
        max_size=settings.azure_postgres_pool_max,
        pool_timeout=settings.azure_postgres_pool_timeout_seconds,
        statement_timeout_ms=settings.azure_postgres_statement_timeout_ms,
    )


def azure_adapters(settings: Settings, clients: dict[str, Any] | None = None) -> Adapters:
    from nodo_api.adapters.azure.blob import AzureBlobStore
    from nodo_api.adapters.azure.exports_pg import PgExportStore
    from nodo_api.adapters.azure.graph_pg import PgGraphStore
    from nodo_api.adapters.azure.openai_extractor import AzureOpenAiExtractor
    from nodo_api.adapters.azure.openai_llm import AzureOpenAiEmbedder, AzureOpenAiLlm
    from nodo_api.adapters.azure.postgres import (
        PgAuditLog,
        PgJobOutbox,
        PgMeetingRepository,
        PgTranscriptRepository,
    )
    from nodo_api.adapters.azure.retention_pg import PgRetentionStore
    from nodo_api.adapters.azure.search import AzureSearchRetriever
    from nodo_api.adapters.azure.servicebus import ServiceBusJobQueue
    from nodo_api.adapters.azure.speech import AzureSpeechToText
    from nodo_api.adapters.azure.speech_jobs_pg import PgSpeechJobStore

    c = clients or azure_clients(settings)
    db = postgres_database(settings, c["credential"])
    embedder = Guarded(
        AzureOpenAiEmbedder(
            c["openai"], settings.openai_embedding_deployment, settings.openai_embedding_dimensions
        ),
        make_guard("openai-embedding", limit=settings.bulkhead_embedding),
        ("embed",),
    )
    chat = make_guard("openai-chat", limit=settings.bulkhead_llm, retry=INTERACTIVE_RETRY)
    extraction = make_guard(
        "openai-extraction", limit=settings.bulkhead_extraction, retry=BATCH_RETRY
    )
    # Speech is not retried here: a retry would submit a second paid job for the same audio.
    speech = make_guard("speech", limit=settings.bulkhead_speech, retry=RetryPolicy(attempts=1))
    return Adapters(
        meetings=PgMeetingRepository(db),
        transcripts=PgTranscriptRepository(db),
        blobs=Guarded(
            AzureBlobStore(
                settings.azure_storage_account,
                c["blob"],
                upload_seconds=settings.upload_session_seconds,
            ),
            make_guard("storage", limit=settings.bulkhead_storage, retry=INTERACTIVE_RETRY),
            ("create_upload_session", "create_read_url", "erase"),
        ),
        jobs=Guarded(
            ServiceBusJobQueue(
                PgJobOutbox(db),
                lambda: c["servicebus"].get_queue_sender(settings.azure_servicebus_queue),
            ),
            make_guard("servicebus", limit=settings.bulkhead_servicebus, retry=INTERACTIVE_RETRY),
            ("publish_once", "publish_later"),
        ),
        retriever=Guarded(
            AzureSearchRetriever(c["search"], embedder),
            make_guard("search", limit=settings.bulkhead_search, retry=INTERACTIVE_RETRY),
            ("index", "search", "erase"),
        ),
        graph=PgGraphStore(db),
        llm=Guarded(
            AzureOpenAiLlm(c["openai"], settings.openai_chat_deployment), chat, ("answer",)
        ),
        audit=PgAuditLog(db),
        speech=Guarded(
            AzureSpeechToText(settings.azure_speech_endpoint, c["speech_token"]),
            speech,
            ("transcribe", "submit", "poll", "cancel"),
        ),
        extractor=Guarded(
            AzureOpenAiExtractor(c["openai"], settings.openai_chat_deployment),
            extraction,
            ("extract",),
        ),
        # Only what this replica can not work without. Search, the model and the queue are not here:
        # their failure degrades single features (the guards report it); taking the replica out of
        # rotation would turn a partial outage into a total one.
        readiness=(("database", db.ping),) if hasattr(db, "ping") else (),
        retention=PgRetentionStore(db),
        exports=PgExportStore(db),
        speech_jobs=PgSpeechJobStore(db),
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
        staged_speech=adapters.speech,
        speech_jobs=adapters.speech_jobs,
        jobs=adapters.jobs,
        poll_seconds=settings.speech_poll_seconds,
        poll_max_seconds=settings.speech_poll_max_seconds,
        speech_timeout_seconds=settings.speech_timeout_seconds,
    )
    exports = ExportService(
        adapters.exports,
        adapters.meetings,
        adapters.transcripts,
        adapters.graph,
        adapters.blobs,
        adapters.jobs,
        adapters.audit,
    )
    return WorkerRuntime(
        ingestion,
        lambda idle: clients["servicebus"].get_queue_receiver(
            settings.azure_servicebus_queue, max_wait_time=idle
        ),
        exports,
    )
