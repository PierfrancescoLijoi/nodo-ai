"""Builds the local backend: the database, the models and the worker that runs on this computer."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nodo_api.adapters.local.answers import SqliteAnswerStore
from nodo_api.adapters.local.blob import LocalBlobStore
from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.documents.parser import SubprocessDocumentParser
from nodo_api.adapters.local.documents.sniff import check_limits, sniff
from nodo_api.adapters.local.embedder import DEFAULT_MODEL, LocalEmbedder
from nodo_api.adapters.local.exports import SqliteExportStore
from nodo_api.adapters.local.fragment_index import SqliteFragmentIndex
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.imports import SqliteImportJobRepository
from nodo_api.adapters.local.llm import LocalExtractor, LocalFragmentExtractor, LocalLlm
from nodo_api.adapters.local.maintenance import LocalIndexService, SqliteMaintenanceStore
from nodo_api.adapters.local.models import GpuArbiter, LlamaServer, LocalModelPaths, data_dir
from nodo_api.adapters.local.queue import LocalWorkerLoop, SqliteJobQueue
from nodo_api.adapters.local.repos import (
    SqliteAuditLog,
    SqliteMeetingRepository,
    SqliteTranscriptRepository,
)
from nodo_api.adapters.local.reranker import OnnxReranker
from nodo_api.adapters.local.retention import SqliteRetentionStore
from nodo_api.adapters.local.search import SqliteRetriever
from nodo_api.adapters.local.source_blobs import LocalSourceBlobStore
from nodo_api.adapters.local.sources import SqliteFragmentRepository, SqliteSourceRepository
from nodo_api.adapters.local.speech import LocalSpeechToText
from nodo_api.adapters.local.sync import LocalSyncService
from nodo_api.adapters.local.watch_folders import SqliteWatchFolderRepository
from nodo_api.api.v1.sync_schemas import SyncSource
from nodo_api.auth import LOCAL_USER, LOCAL_WORKSPACE
from nodo_api.config import Settings
from nodo_api.errors import ModelUnavailable
from nodo_api.services.entity_index import EntityIndexer
from nodo_api.services.exports import ExportService
from nodo_api.services.extraction import ChunkedExtractor
from nodo_api.services.folder_watch import FolderWatcher
from nodo_api.services.fragment_extraction import ChunkedFragmentExtractor, extraction_version
from nodo_api.services.index_maintenance import IndexMaintenanceService
from nodo_api.services.ingestion import IngestionService
from nodo_api.services.retention import RetentionService
from nodo_api.services.source_import import SourceImportService
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


class EngineAlreadyRunning(RuntimeError):
    """Another engine holds the lock of this data folder."""


class EngineLock:
    """An exclusive OS lock on a file in the data folder, held while the engine runs.

    The operating system drops it when the process dies, so a crash never leaves it stale."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def acquire(self) -> None:
        handle = open(self._path, "a+b")  # noqa: SIM115 - held open for the life of the engine
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise EngineAlreadyRunning(
                f"Another NODO engine is already using the data folder {self._path.parent}"
            ) from None
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            self._file.close()  # closing the handle releases the lock on every platform
            self._file = None


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
    reranker: OnnxReranker | None
    retention: RetentionService
    index: LocalIndexService
    warm_up: bool = False
    lock: EngineLock | None = None
    imports: SourceImportService | None = None
    watcher: FolderWatcher | None = None
    watch_poll_seconds: float = 30.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _watch_thread: threading.Thread | None = None

    def start(self) -> None:
        # One engine per data folder; the second fails here, before it touches any claim.
        if self.lock is not None:
            self.lock.acquire()
        # Claims still held come from an engine that died: free them before the worker starts.
        try:
            released = self.queue.release_claims()
            if released:
                logger.warning("released_stale_claims count=%s", released)
        except Exception as error:  # the one hour stale-claim rule still recovers them later
            logger.error("release_claims_failed type=%s", type(error).__name__)
        # Imports a crash left running are queued again (their stages are idempotent).
        if self.imports is not None:
            try:
                self.imports.resume_interrupted()
            except Exception as error:
                logger.error("resume_imports_failed type=%s", type(error).__name__)
        self.loop.start()
        threading.Thread(target=self._retention_loop, name="nodo-retention", daemon=True).start()
        if self.watcher is not None:  # after the engine lock: one engine scans a data folder
            self._watch_thread = threading.Thread(
                target=self.watcher.run,
                args=(self._stop, self.watch_poll_seconds),
                name="nodo-watch",
                daemon=True,
            )
            self._watch_thread.start()
        if self.warm_up:
            threading.Thread(target=self._warm, name="nodo-warm-up", daemon=True).start()

    def _warm(self) -> None:
        """The search model takes about 30 s to load; do it while the window opens."""
        try:
            self.embedder.embed_passages(["warm up"])
            if self.reranker is not None:
                self.reranker.score("warm up", ["warm up"])
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
        if self._watch_thread is not None:  # a scan in progress ends its file, then the loop
            self._watch_thread.join(timeout=30)
        if self.server is not None:
            self.server.stop()
        self.db.close()
        if self.lock is not None:
            self.lock.release()


def document_screen(settings: Settings):
    """sniff and limits before every parse (Task 4): nothing heavy runs on a refused file."""

    def screen(path: Path, name: str) -> None:
        check_limits(
            path,
            sniff(path, name),
            max_bytes=settings.local_import_max_bytes,
            max_pages=settings.local_import_max_pages,
            max_ratio=settings.local_import_max_ratio,
            max_uncompressed_bytes=settings.local_import_max_uncompressed_bytes,
            memory_mb=settings.local_parser_memory_mb,
        )

    return screen


# E5 scores are compressed: related and unrelated texts sit between about 0.68 and 0.86. 0.78 sits
# just above the best unrelated question and below the median right entity (admin calibrate-entities).
LOCAL_ENTITY_SIMILARITY = 0.78
# Measured on E5 (admin calibrate-answer-cache): look-alikes the words do not separate reach 0.95,
# repeats are 0.93 to 0.98, so about half of the repeats are caught with no false hit in the set.
LOCAL_ANSWER_SIMILARITY = 0.96


def local_adapters(settings: Settings):
    from nodo_api.bootstrap import Adapters, build_answer_cache

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
    reranker = (
        OnnxReranker(models.reranker_dir)
        if settings.local_reranker_enabled and OnnxReranker.installed(models.reranker_dir)
        else None
    )
    retriever = SqliteRetriever(
        db,
        embedder,
        min_margin=settings.local_search_margin,
        reranker=reranker,
        rerank_candidates=settings.local_rerank_candidates,
    )
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
        fragment_extractor: Any = ChunkedFragmentExtractor(
            LocalFragmentExtractor(server, arbiter), settings.local_extract_window_tokens
        )
        doc_version: str | None = extraction_version(models.llm_gguf.stem[:80])
    else:
        llm, extractor = Unavailable("language model"), Unavailable("language model")
        fragment_extractor, doc_version = Unavailable("language model"), None

    entity_indexer = EntityIndexer(
        graph,
        embedder,
        min_similarity=settings.entity_min_similarity or LOCAL_ENTITY_SIMILARITY,
    )
    answers = build_answer_cache(
        settings,
        SqliteAnswerStore(db),
        entity_indexer,
        "local",
        f"{models.llm_gguf.stem if models.llm_gguf else 'none'}|{DEFAULT_MODEL}",
        LOCAL_ANSWER_SIMILARITY,
    )
    meetings = SqliteMeetingRepository(db)
    transcripts = SqliteTranscriptRepository(db)
    audit = SqliteAuditLog(db)
    exports = SqliteExportStore(db)
    maintenance = SqliteMaintenanceStore(db)
    ingestion = IngestionService(
        meetings,
        transcripts,
        blobs,
        retriever,
        speech,
        settings.speech_candidate_locales,
        extractor,
        graph,
        entity_indexer=entity_indexer,
        answers=answers,
    )
    sources = SqliteSourceRepository(db)
    fragments = SqliteFragmentRepository(db)
    imports = SqliteImportJobRepository(db)
    fragment_index = SqliteFragmentIndex(db, embedder, min_margin=settings.local_search_margin)
    source_blobs = LocalSourceBlobStore(
        root / "sources",
        max_bytes=settings.local_import_max_bytes,
        upload_seconds=settings.upload_session_seconds,
    )

    source_import = SourceImportService(
        sources,
        fragments,
        imports,
        source_blobs,
        SubprocessDocumentParser(
            settings.local_parser_timeout_seconds, settings.local_parser_memory_mb
        ),
        fragment_index,
        fragment_extractor,
        graph,
        queue,
        audit,
        extraction_version=doc_version,
        answers=answers,
        control=queue,
        screen=document_screen(settings),
        token_fits=embedder.within_ceiling,
        speech=speech,
        speech_locales=settings.speech_candidate_locales,
    )
    watcher = FolderWatcher(SqliteWatchFolderRepository(db), source_import, source_blobs, root)
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
                imports=source_import,
            ),
        ),
        models,
        server,
        sync,
        embedder,
        reranker,
        RetentionService(
            SqliteRetentionStore(db),
            blobs,
            SqliteAuditLog(db),
            sources=sources,
            source_blobs=source_blobs,
        ),
        LocalIndexService(
            IndexMaintenanceService(
                maintenance, transcripts, retriever, graph, queue, audit, entity_indexer, answers
            )
        ),
        settings.local_warm_up,
        EngineLock(root / "engine.lock"),
        source_import,
        watcher,
        settings.watch_poll_seconds,
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
        maintenance=maintenance,
        entity_indexer=entity_indexer,
        answers=answers,
        sources=sources,
        fragments=fragments,
        fragment_index=fragment_index,
        imports=imports,
        fragment_extractor=fragment_extractor,
        extraction_version=doc_version,
        source_blobs=source_blobs,
        source_import=source_import,
        folder_watcher=watcher,
    )
