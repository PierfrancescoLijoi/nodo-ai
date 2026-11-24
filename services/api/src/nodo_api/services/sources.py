from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime

from nodo_api.domain.sources import SourceKind
from nodo_api.errors import NotFoundError, UseMeetingsApi
from nodo_api.ports import (
    AuditLog,
    CollectionRepository,
    FragmentIndex,
    FragmentRepository,
    GraphStore,
    ImportJobRepository,
    JobControl,
    PageRepository,
    SourceBlobStore,
    SourceRepository,
    WatchFolderRepository,
)
from nodo_api.services.answer_cache import AnswerCache, invalidate
from nodo_api.services.source_import import IMPORT_SOURCE

logger = logging.getLogger("nodo_sources")
DELETING = "deleting"  # tombstoned, erase not finished: finish_deletes runs it again
DELETED = "deleted"


class SourceService:
    """Operations that must erase every derived copy of a non-meeting source.

    The import ports are optional only for the older tests; the container builds the service
    only when the engine has all of them (main.py)."""

    def __init__(
        self,
        sources: SourceRepository,
        fragments: FragmentRepository,
        graph: GraphStore,
        answers: AnswerCache | None,
        audit: AuditLog,
        clock: Callable[[], datetime],
        *,
        fragment_index: FragmentIndex | None = None,
        blobs: SourceBlobStore | None = None,
        imports: ImportJobRepository | None = None,
        jobs: JobControl | None = None,
        watch: WatchFolderRepository | None = None,
        collections: CollectionRepository | None = None,
        pages: PageRepository | None = None,
    ) -> None:
        self._sources = sources
        self._fragments = fragments
        self._graph = graph
        self._answers = answers
        self._audit = audit
        self._clock = clock
        self._index = fragment_index
        self._blobs = blobs
        self._imports = imports
        self._jobs = jobs
        self._watch = watch
        self._collections = collections
        self._pages = pages

    def delete(self, workspace_id: str, user_id: str, source_id: str) -> None:
        """Tombstone (state `deleting`, title, origin note and content hash forgotten), stop the
        import, audit, then erase search rows, graph evidence, fragments, original files, upload
        links, the import job and its queue messages, empty the answer cache (also when a step
        fails) and mark the delete finished (state `deleted`). Safe to retry at any point:
        every step is idempotent, the audit event is keyed by the source so it is written once,
        and an unfinished delete is run again at engine start (`finish_deletes`)."""
        source = self._sources.get_owned(workspace_id, user_id, source_id, include_tombstoned=True)
        if source.kind is SourceKind.MEETING:
            raise UseMeetingsApi("use the meetings API")

        if source.tombstoned_at is None or source.state != DELETED:
            self._sources.save(
                workspace_id,
                replace(
                    source,
                    tombstoned_at=source.tombstoned_at or self._clock(),
                    state=DELETING,
                    title=None,
                    origin=None,
                    content_hash=None,
                ),
            )
        try:
            self._erase(workspace_id, user_id, source_id, source.kind)
        except Exception:
            # Cached answers can quote the document: empty them although a step failed (the
            # retry or finish_deletes erases the rest).
            with suppress(Exception):
                invalidate(self._answers, workspace_id, user_id)
            raise
        dead = self._sources.get_owned(workspace_id, user_id, source_id, include_tombstoned=True)
        self._sources.save(workspace_id, replace(dead, state=DELETED))
        if self._index is not None:
            self._index.compact()  # last: old page images of the erased rows leave the disk

    def _erase(self, workspace_id: str, user_id: str, source_id: str, kind: SourceKind) -> None:
        self._stop_import(workspace_id, user_id, source_id)
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"nodo:source_deleted:{workspace_id}:{source_id}")
        self._audit.append(
            workspace_id, "source_deleted", {"kind": kind.value}, event_id=str(event_id)
        )
        if self._index is not None:  # the memory index has no trigger backstop
            self._index.erase_source(workspace_id, source_id)
        database = getattr(self._fragments, "_db", None)
        page_database = getattr(self._pages, "_db", None) if self._pages is not None else None
        if database is not None and database is page_database:
            # Local adapters share one SQLite transaction. A crash cannot commit the fragment
            # erasure while leaving source text in a Wiki revision (ADR-031).
            with database.transaction():
                self._graph.erase(workspace_id, source_id)
                self._pages.redact_source(workspace_id, source_id)
                self._fragments.erase(workspace_id, source_id)
        else:
            self._graph.erase(workspace_id, source_id)
            if self._pages is not None:
                self._pages.redact_source(workspace_id, source_id)
            self._fragments.erase(workspace_id, source_id)
        if self._blobs is not None:
            self._blobs.erase(workspace_id, source_id)  # every version, .part files, open links
        if self._imports is not None:
            self._imports.delete(workspace_id, source_id)
        if self._jobs is not None:  # their keys carry a prefix of the content hash
            self._jobs.discard(workspace_id, IMPORT_SOURCE, f"{source_id}:")
        if self._watch is not None:  # the path keeps its hash: these bytes stay deleted
            self._watch.unlink_source(source_id)
        if self._collections is not None:  # the memory store has no tombstone trigger
            self._collections.forget_source(workspace_id, source_id)
        # After the last change, so an answer computed before it is never stored.
        invalidate(self._answers, workspace_id, user_id)

    def finish_deletes(self, limit: int = 500) -> int:
        """Run again every delete that failed or was interrupted after its tombstone (called at
        engine start). Returns how many finished; a failure is logged by type and kept for the
        next start."""
        finished = 0
        for source in self._sources.unfinished_deletes(limit):
            try:
                self.delete(source.workspace_id, source.owner_id, source.id)
            except Exception as error:
                logger.warning("source_delete_unfinished type=%s", type(error).__name__)
                continue
            finished += 1
        return finished

    def _stop_import(self, workspace_id: str, user_id: str, source_id: str) -> None:
        """A running import sees the flag (and the tombstone) at its next check and erases what it
        wrote; its queue messages are removed after the erase."""
        if self._imports is None:
            return
        with suppress(NotFoundError):
            self._imports.request_cancel(workspace_id, user_id, source_id)
