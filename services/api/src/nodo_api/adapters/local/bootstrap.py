"""Builds the local backend: the database, the models and the worker that runs on this computer."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nodo_api.adapters.local.blob import LocalBlobStore
from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.embedder import LocalEmbedder
from nodo_api.adapters.local.exports import SqliteExportStore
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.llm import LocalExtractor, LocalLlm
from nodo_api.adapters.local.models import GpuArbiter, LlamaServer, LocalModelPaths, data_dir
from nodo_api.adapters.local.queue import LocalWorkerLoop, SqliteJobQueue
from nodo_api.adapters.local.repos import (
    SqliteAuditLog,
    SqliteMeetingRepository,
    SqliteTranscriptRepository,
)
from nodo_api.adapters.local.retention import SqliteRetentionStore
from nodo_api.adapters.local.search import SqliteRetriever
from nodo_api.adapters.local.speech import LocalSpeechToText
from nodo_api.adapters.local.sync import LocalSyncService
from nodo_api.api.v1.sync_schemas import SyncSource
from nodo_api.auth import LOCAL_USER, LOCAL_WORKSPACE
from nodo_api.config import Settings
from nodo_api.errors import ModelUnavailable
from nodo_api.services.exports import ExportService
from nodo_api.services.extraction import ChunkedExtractor
from nodo_api.services.ingestion import IngestionService
from nodo_api.services.retention import RetentionService
from nodo_api.worker import Worker

logger = logging.getLogger("nodo_local")
RETENTION_INTERVAL_SECONDS = 3600


class Unavailable:
    """Stands in for a model that is not installed: every call says so, instead of crashing later."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def refuse(*args: Any, **kwargs: Any) -> Any:
            logger.warning("model_unavailable what=%s", self._what)
            raise ModelUnavailable

        return refuse


@dataclass
class LocalRuntime:
    """What has to start and stop with the app: the job thread and the language model server."""

    db: LocalDatabase
    blobs: LocalBlobStore
    queue: SqliteJobQueue
    loop: LocalWorkerLoop
    models: LocalModelPaths
    server: LlamaServer | None
    sync: LocalSyncService
    embedder: LocalEmbedder
    retention: RetentionService
    warm_up: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self.loop.start()
        threading.Thread(target=self._retention_loop, name="nodo-retention", daemon=True).start()
        if self.warm_up:
            threading.Thread(target=self._warm, name="nodo-warm-up", daemon=True).start()

    def _warm(self) -> None:
        """The search model takes about 30 s to load; do it while the window opens."""
        try:
            self.embedder.embed_passages(["warm up"])
        except Exception as error:  # a missing model is reported when it is needed, not here
            logger.warning("warm_up_skipped type=%s", type(error).__name__)

    def _retention_loop(self) -> None:
        """Erase audio whose retention period has ended: at start, then every hour."""
        while True:
            try:
                self.retention.sweep_all()
            except Exception as error:
                logger.error("retention_sweep_failed type=%s", type(error).__name__)
            if self._stop.wait(RETENTION_INTERVAL_SECONDS):
                return

    def stop(self) -> None:
        self._stop.set()
        self.loop.stop()
        if self.server is not None:
            self.server.stop()
        self.db.close()


def local_adapters(settings: Settings):
    from nodo_api.bootstrap import Adapters

    root = Path(settings.local_data_dir) if settings.local_data_dir else data_dir()
    root.mkdir(parents=True, exist_ok=True)
    models = LocalModelPaths.discover(settings.local_models_dir)
    db = LocalDatabase(root / "nodo.db")
    arbiter = GpuArbiter()
    blobs = LocalBlobStore(
        root / "audio",
        db,
        lambda: settings.local_public_url,
        upload_seconds=settings.upload_session_seconds,
    )
    embedder = LocalEmbedder(models.embedding_dir)
    retriever = SqliteRetriever(db, embedder, min_margin=settings.local_search_margin)
    graph = SqliteGraphStore(db)
    queue = SqliteJobQueue(db)
    speech = LocalSpeechToText(models, arbiter)

    server: LlamaServer | None = None
    if models.llama_server is not None and models.llm_gguf is not None:
        server = LlamaServer(
            models.llama_server,
            models.llm_gguf,
            root / "llama.pid",
            context=settings.local_llm_context,
        )
        llm: Any = LocalLlm(server, arbiter)
        extractor: Any = ChunkedExtractor(
            LocalExtractor(server, arbiter), settings.local_extract_window_tokens
        )
    else:
        llm, extractor = Unavailable("language model"), Unavailable("language model")

    meetings = SqliteMeetingRepository(db)
    transcripts = SqliteTranscriptRepository(db)
    audit = SqliteAuditLog(db)
    exports = SqliteExportStore(db)
    ingestion = IngestionService(
        meetings,
        transcripts,
        blobs,
        retriever,
        speech,
        settings.speech_candidate_locales,
        extractor,
        graph,
    )
    source = SyncSource(
        engine="local",
        speech=f"whisper-{models.whisper_model}",
        language_model=models.llm_gguf.stem[:80] if models.llm_gguf else "",
    )
    sync = LocalSyncService(db, meetings, transcripts, graph, source, LOCAL_WORKSPACE, LOCAL_USER)
    runtime = LocalRuntime(
        db,
        blobs,
        queue,
        LocalWorkerLoop(
            queue,
            Worker(
                ingestion,
                exports=ExportService(exports, meetings, transcripts, graph, blobs, queue, audit),
            ),
        ),
        models,
        server,
        sync,
        embedder,
        RetentionService(SqliteRetentionStore(db), blobs, SqliteAuditLog(db)),
        settings.local_warm_up,
    )
    return Adapters(
        meetings,
        transcripts,
        blobs,
        queue,
        retriever,
        graph,
        llm,
        SqliteAuditLog(db),
        speech,
        extractor,
        runtime,
        retention=SqliteRetentionStore(db),
        exports=exports,
    )
