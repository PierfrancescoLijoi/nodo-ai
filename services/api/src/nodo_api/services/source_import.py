"""Source import: turn an uploaded or watched file into a versioned Source with Sections, Fragments,
a search index and graph evidence (FR-1, FR-2, FR-3; ADR-029).

It mirrors the ingestion service. Every stage is idempotent and resumable: a retry reuses the
version that was already stored, so a model failure never costs a new parse. Deletion and cancel
win at every step: after each stage, and before each batch of extraction windows, the source and the
cancel flag are read again, and a deleted or cancelled import erases what it wrote (index, graph,
fragments; for a deletion also the original file and the file name). Job state changes are
compare-and-set, so a late worker write never turns a cancelled or failed job back.

Nothing here logs or audits document text, a title or a file name: logs carry states, codes and
counts, the audit carries the kind."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodo_api.domain.documents import ParseError, build_sections_and_fragments
from nodo_api.domain.graph import (
    EntityType,
    Extraction,
    ExtractionError,
    SourceRef,
    validate_extraction,
)
from nodo_api.domain.models import Locale
from nodo_api.domain.source_files import safe_filename
from nodo_api.domain.sources import (
    ACTIVE_STATES,
    CLEAR,
    MAX_EMBEDDING_TOKENS,
    DuplicateContentError,
    Fragment,
    ImportJob,
    Section,
    Source,
    SourceKind,
    estimate_tokens,
)
from nodo_api.errors import ConflictError, NotFoundError, ValidationFailed
from nodo_api.ports import (
    AuditLog,
    DocumentExtractor,
    DocumentParser,
    FragmentIndex,
    FragmentRepository,
    GraphStore,
    ImportJobRepository,
    JobControl,
    JobQueue,
    SourceBlobStore,
    SourceRepository,
)
from nodo_api.services.answer_cache import AnswerCache, invalidate
from nodo_api.services.extraction import merge

logger = logging.getLogger("nodo_import")

IMPORT_SOURCE = "import_source"  # the worker job type (nodo_api.worker.IMPORT_SOURCE)
ACTIVE = ACTIVE_STATES
NEW_VERSION = "new_version"  # job step: this attempt stored a new version (erase it on failure)
ENDED = ("failed", "cancelled")
KNOWN_DECISIONS = 20
# Extraction re-checks delete and cancel between batches of this many fragments: about one model
# window at the target fragment size, so a stop is honoured after at most one more model call.
BATCH_FRAGMENTS = 8
MAX_ORIGIN_CHARS = 500
_CODE = re.compile(r"[a-z0-9_]{1,64}")
# Audio and video are Task 14; meetings are created by the meeting service.
_KINDS = {
    ".pdf": SourceKind.DOCUMENT,
    ".docx": SourceKind.DOCUMENT,
    ".xlsx": SourceKind.DOCUMENT,
    ".txt": SourceKind.DOCUMENT,
    ".pptx": SourceKind.DECK,
    ".html": SourceKind.WEB,
    ".htm": SourceKind.WEB,
    ".md": SourceKind.NOTE,
    ".markdown": SourceKind.NOTE,
}


class _Stop(Exception):
    """The import ended early (deleted, cancelled or superseded); compensation is done."""


def job_key(source_id: str, content_hash: str, attempt: int) -> str:
    """The queue key of one import attempt. The first is `source:hash16`; every retry, reimport or
    resume adds its attempt number, because a finished or cancelled key is never delivered again."""
    key = f"{source_id}:{content_hash[:16]}"
    return f"{key}:{attempt}" if attempt else key


def _kind_for(name: str, kind: SourceKind | None) -> SourceKind:
    inferred = _KINDS.get(Path(name).suffix.lower())
    if inferred is None or kind in (SourceKind.MEETING, SourceKind.AUDIO):
        raise ValidationFailed
    return kind or inferred


def _batches(fragments: Sequence[Fragment]) -> list[tuple[Fragment, ...]]:
    """Consecutive runs of at most BATCH_FRAGMENTS fragments in reading order (also inside one
    large section: a document without headings is one section)."""
    ordered = sorted(fragments, key=lambda f: (f.ordinal, f.id))
    return [
        tuple(ordered[i : i + BATCH_FRAGMENTS]) for i in range(0, len(ordered), BATCH_FRAGMENTS)
    ]


class SourceImportService:
    def __init__(
        self,
        sources: SourceRepository,
        fragments: FragmentRepository,
        imports: ImportJobRepository,
        blobs: SourceBlobStore,
        parser: DocumentParser,
        index: FragmentIndex,
        extractor: DocumentExtractor,
        graph: GraphStore,
        jobs: JobQueue,
        audit: AuditLog,
        *,
        extraction_version: str | None,
        answers: AnswerCache | None = None,
        control: JobControl | None = None,
        screen: Callable[[Path, str], None] | None = None,
        token_fits: Callable[[list[str]], list[bool]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = sources
        self._fragments = fragments
        self._imports = imports
        self._blobs = blobs
        self._parser = parser
        self._index = index
        self._extractor = extractor
        self._graph = graph
        self._jobs = jobs
        self._audit = audit
        # None: no local model, so documents are imported searchable and extracted later.
        self._version = extraction_version
        self._answers = answers
        self._control = control
        # sniff and check_limits (Task 4): they run before every parse, never inside the worker.
        self._screen = screen
        self._token_fits = token_fits
        # Times this service writes (created_at, tombstones). Job and version rows keep the wall
        # clock of their stores (Task 5 decision): they are progress, not facts a test fakes.
        self._clock = clock

    # ------------------------------------------------------------------------------ requests

    def create(
        self,
        workspace_id: str,
        user_id: str,
        *,
        filename: str,
        size_bytes: int,
        kind: SourceKind | None,
        origin_note: str | None,
        idempotency_key: str | None,
    ) -> tuple[Source, Any]:
        """A queued source and a one-time upload link for version 1. The same idempotency key
        with the same request gives the same source and a fresh link; with another request, or
        once the file is uploaded, ConflictError."""
        try:
            name = safe_filename(filename)
        except ValueError:
            raise ValidationFailed from None
        kind = _kind_for(name, kind)
        if type(size_bytes) is not int or size_bytes < 1:
            raise ValidationFailed
        if origin_note is not None and (
            len(origin_note) > MAX_ORIGIN_CHARS
            or any(unicodedata.category(c) in {"Cc", "Cs"} for c in origin_note)
        ):
            raise ValidationFailed
        if idempotency_key is None:
            source_id = f"src_{uuid.uuid4().hex}"
        else:
            # The key names the source, so a replay finds it without a separate key table.
            key = "\x00".join((workspace_id, user_id, idempotency_key)).encode("utf-8", "replace")
            source_id = f"src_{hashlib.sha256(key).hexdigest()[:32]}"
        source = Source(
            source_id, workspace_id, user_id, kind, name, "queued", self._clock(), origin_note
        )
        created = True
        try:
            self._sources.add(workspace_id, source)
        except ValueError:
            if idempotency_key is None:
                raise
            try:
                source = self._sources.get_owned(workspace_id, user_id, source_id)
            except NotFoundError:
                raise ConflictError from None
            if (source.kind, source.title, source.origin) != (kind, name, origin_note):
                raise ConflictError from None  # same key, another request
            if source.content_hash is not None:
                raise ConflictError from None  # uploaded already: no new link for version 1
            created = False
        try:
            self._imports.create(workspace_id, user_id, source_id)
            session = self._blobs.create_upload_session(
                workspace_id, user_id, source_id, name, size_bytes
            )
        except BaseException:
            if created:  # a refused link (size, disk, busy) must not leave a named, queued row
                self._discard(workspace_id, source)
            raise
        if created:
            self._audit.append(workspace_id, "source_import_created", {"kind": kind.value})
        return source, session

    def complete(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """The upload is finished: hash it and queue the import. Bytes the owner already has in
        another live source answer with that source's job (unchanged; a failed or cancelled one is
        retried) and this source is removed. Repeated calls are idempotent."""
        source = self._sources.get_owned(workspace_id, user_id, source_id, include_tombstoned=True)
        if source.kind is SourceKind.MEETING:
            raise NotFoundError
        if source.tombstoned_at is not None:
            # Deleted while the body arrived: the store forgets erased ids on restart.
            self._blobs.erase(workspace_id, source_id)
            raise NotFoundError
        if source.content_hash is not None:
            job = self._imports.get_owned(workspace_id, user_id, source_id)
            if job.state in ACTIVE:
                self._publish(workspace_id, source_id, source.content_hash, job.attempt)
            return job
        original = self._blobs.path_for(workspace_id, source_id, 1)
        if not original.is_file():
            raise ConflictError  # the upload has not finished
        _, digest = self._blobs.size_and_sha256(original)
        for _ in range(3):  # each turn follows a race: a twin that appears or one that vanishes
            twin = self._sources.find_by_hash(workspace_id, user_id, digest)
            if twin is None:
                try:
                    source = self._sources.save(
                        workspace_id, replace(source, content_hash=digest), live_only=True
                    )
                except NotFoundError:
                    self._blobs.erase(workspace_id, source_id)  # deleted meanwhile
                    raise
                except DuplicateContentError:
                    continue  # the same bytes completed at the same moment: answer with them
                job = self._imports.get_owned(workspace_id, user_id, source_id)
                self._publish(workspace_id, source_id, digest, job.attempt)
                return job
            try:
                answer = self._unchanged(workspace_id, user_id, twin)
            except NotFoundError:
                continue  # the twin was deleted meanwhile: these bytes are new again
            self._discard(workspace_id, source)
            return answer
        raise ConflictError

    def reimport(
        self, workspace_id: str, user_id: str, source_id: str, new_file: Path
    ) -> ImportJob:
        """New bytes for an existing source: the same bytes are a no-op, bytes of another live
        source answer with that source, different bytes become the next version."""
        source = self._owned(workspace_id, user_id, source_id)
        path = Path(new_file)
        is_junction = getattr(path, "is_junction", lambda: False)
        if path.is_symlink() or is_junction() or not path.is_file():
            raise ValidationFailed  # the store copy would follow the link
        try:
            name = safe_filename(path.name)
        except ValueError:
            raise ValidationFailed from None
        _kind_for(name, None)
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        if job.state in ACTIVE:
            raise ConflictError  # one import of a source at a time
        _, digest = self._blobs.size_and_sha256(path)
        if digest == source.content_hash:
            stale = self._stale(workspace_id, user_id, source, job)
            return self._rerun(workspace_id, source, job) if stale else job
        twin = self._sources.find_by_hash(workspace_id, user_id, digest)
        if twin is not None:
            return self._unchanged(workspace_id, user_id, twin)
        # Reserve the source first: a second reimport now gets ConflictError, so two copies
        # never race for the same file version.
        reserved = self._imports.update(
            workspace_id, source_id, state="queued", expected_state=job.state
        )
        try:
            version = len(self._file_versions(workspace_id, source_id)) + 1
            stored = self._blobs.adopt(workspace_id, source_id, version, path)
            _, digest = self._blobs.size_and_sha256(stored)  # what was copied is what counts
            fresh = self._sources.get_for_processing(workspace_id, source_id)
            source = self._sources.save(
                workspace_id,
                replace(fresh, content_hash=digest, title=name, state="queued"),
                live_only=True,
            )
        except NotFoundError:
            self._blobs.erase(workspace_id, source_id)  # deleted during the copy
            raise
        except BaseException as error:
            with suppress(ConflictError, NotFoundError):  # give the source back as it was
                self._imports.update(
                    workspace_id, source_id, state=job.state, expected_state="queued"
                )
            if not isinstance(error, DuplicateContentError):
                raise
            twin = self._sources.find_by_hash(workspace_id, user_id, digest)
            if twin is None:
                raise ConflictError from None
            return self._unchanged(workspace_id, user_id, twin)
        self._audit.append(workspace_id, "source_reimported", {"kind": source.kind.value})
        return self._rerun(workspace_id, source, reserved)

    def cancel(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """Stop a queued or running import. One nobody has started ends cancelled now; a running
        one stops at its next check and compensates."""
        source = self._owned(workspace_id, user_id, source_id)
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        if job.state not in ACTIVE:
            raise ConflictError
        self._imports.request_cancel(workspace_id, user_id, source_id)
        unclaimed = source.content_hash is None or (
            self._control is not None
            and self._control.cancel(
                workspace_id, job_key(source_id, source.content_hash, job.attempt), IMPORT_SOURCE
            )
        )
        if unclaimed:
            with suppress(_Stop):
                self._cancelled(workspace_id, source_id, user_id)
        self._audit.append(workspace_id, "source_import_cancelled", {"kind": source.kind.value})
        return self._imports.get_owned(workspace_id, user_id, source_id)

    def retry(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """Queue a failed or cancelled import again (also a ready one whose extraction is not
        current). The stored original is parsed again unless its version is already stored."""
        source = self._owned(workspace_id, user_id, source_id)
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        allowed = job.state in ENDED or self._stale(workspace_id, user_id, source, job)
        if source.content_hash is None or not allowed:
            raise ConflictError
        job = self._rerun(workspace_id, source, job)
        self._audit.append(workspace_id, "source_import_retried", {"kind": source.kind.value})
        return job

    def resume_interrupted(self, limit: int = 500) -> int:
        """Queue again the imports a crash left unfinished (called at engine start). A queued job
        is published again under its own key (a no-op when the queue holds it, a delivery when the
        publish never happened); a running one under a fresh key. The stages are idempotent, so a
        duplicate message only finds the job done. Returns the count."""
        resumed = 0
        for job in self._imports.in_progress(limit):
            try:
                source = self._sources.get_for_processing(job.workspace_id, job.source_id)
                if source.content_hash is None:
                    continue
                if job.state == "queued":
                    self._publish(job.workspace_id, job.source_id, source.content_hash, job.attempt)
                    resumed += 1
                    continue
                self._imports.update(
                    job.workspace_id,
                    job.source_id,
                    attempt=job.attempt + 1,
                    expected_state=job.state,
                )
            except (NotFoundError, ConflictError):
                continue
            self._publish(job.workspace_id, job.source_id, source.content_hash, job.attempt + 1)
            resumed += 1
        if resumed:
            logger.warning("imports_resumed count=%s", resumed)
        return resumed

    # ------------------------------------------------------------------------------ worker

    def process(self, workspace_id: str, source_id: str) -> None:
        """Run or resume the import. A message for a finished, unknown or deleted import is a
        no-op. Transient errors (model, dependency, extraction) propagate so the queue retries;
        permanent parse errors end the import failed with their code."""
        try:
            job = self._imports.get_for_processing(workspace_id, source_id)
        except NotFoundError:
            return
        if job.state not in ACTIVE:
            return
        with suppress(_Stop):
            self._run(workspace_id, source_id, job.owner_id, job.state)

    def mark_failed(self, workspace_id: str, source_id: str, reason: str) -> None:
        """End a queued or running import failed with a stable code. A new version this attempt
        stored (partly) is erased, so no partial content stays searchable; a failure before that
        (parse, limits, missing file) keeps the earlier good version searchable and the source
        ready, with the job failed as the warning. The stored original stays for a retry. A
        finished or cancelled job is left alone."""
        code = reason if isinstance(reason, str) and _CODE.fullmatch(reason) else "import_failed"
        try:
            job = self._imports.get_for_processing(workspace_id, source_id)
        except NotFoundError:
            return
        if job.state not in ACTIVE:
            return
        with suppress(_Stop, ConflictError):
            self._check(workspace_id, source_id, job.owner_id, cancel=False)
            # Win the state change first: only then is it this call that compensates.
            self._imports.update(
                workspace_id,
                source_id,
                state="failed",
                step=CLEAR,
                error_code=code,
                expected_state=ACTIVE,
            )
            logger.warning("import_failed code=%s", code)
            self._compensate(workspace_id, source_id, job.owner_id, job.step, "failed")

    # ------------------------------------------------------------------------------ stages

    def _run(self, workspace_id: str, source_id: str, owner: str, entry_state: str) -> None:
        source = self._check(workspace_id, source_id, owner)
        if source.owner_id != owner or source.content_hash is None:
            return
        reused = self._persisted(workspace_id, owner, source)
        if reused is not None:
            sections, fragments, version, parser, size = reused
            previous: frozenset[str] | None = None  # the earlier version is gone: extract all
            # Only a run that reached extraction finished the index of this version; anything
            # earlier (a failed or interrupted index) rewrites it, so old text never stays.
            if entry_state != "extracting":
                self._enter(workspace_id, source_id, "indexing", total=len(fragments))
                self._index_fragments(workspace_id, source_id, owner, fragments)
        else:
            parsed = self._parse(workspace_id, source_id, owner, source)
            if parsed is None:
                return
            sections, fragments, parser, size = parsed
            previous = self._previous_ids(workspace_id, owner, source_id)
            self._enter(workspace_id, source_id, "indexing", total=len(fragments))
            try:
                version = self._fragments.add_version(workspace_id, source_id, sections, fragments)
                self._mark_new_version(workspace_id, source_id)
                self._fragments.record_version(
                    workspace_id,
                    source_id,
                    version,
                    source.content_hash,
                    parser,
                    None,
                    source.title,
                    size,
                )
            except NotFoundError:
                self._check(workspace_id, source_id, owner)  # deleted meanwhile: _Stop
                raise
            # The index takes only the current version: stamp what add_version returned.
            fragments = tuple(replace(f, version=version) for f in fragments)
            self._index_fragments(workspace_id, source_id, owner, fragments)
        self._check(workspace_id, source_id, owner)
        self._extract(workspace_id, source, sections, fragments, previous, (version, parser, size))

    def _parse(self, workspace_id: str, source_id: str, owner: str, source: Source):
        """(sections, fragments, parser, size), or None when the import already ended."""
        assert source.content_hash is not None
        self._enter(workspace_id, source_id, "parsing")
        found = self._original(workspace_id, source_id, source.content_hash)
        if found is None:
            self._check(workspace_id, source_id, owner)
            self.mark_failed(workspace_id, source_id, "original_missing")
            return None
        path, size = found
        name = source.title or "document"
        try:
            if self._screen is not None:
                self._screen(path, name)
            parsed = self._parser.parse(path, declared_name=name)
        except ParseError as error:
            self._check(workspace_id, source_id, owner)
            if error.code != "no_text_layer":
                self.mark_failed(workspace_id, source_id, error.code)
                return None
            self._no_text_layer(workspace_id, source, owner, "none", size)
            return None
        self._check(workspace_id, source_id, owner)
        sections, fragments = build_sections_and_fragments(parsed, source_id, 1, fits=self._fits)
        if not fragments:
            if parsed.no_text_layer:
                self._no_text_layer(workspace_id, source, owner, parsed.parser, size)
            else:
                self.mark_failed(workspace_id, source_id, "empty")
            return None
        return sections, fragments, parsed.parser, size

    def _no_text_layer(
        self, workspace_id: str, source: Source, owner: str, parser: str, size: int
    ) -> None:
        """A scan: ready with no fragments and a warning the health view shows."""
        assert source.content_hash is not None
        self._erase_derived(workspace_id, source.id)  # an earlier version must not stay
        self._record(workspace_id, source, owner, 1, parser, self._version, size)
        self._finish(workspace_id, source.id, owner, "ready", "no_text_layer")

    def _index_fragments(
        self, workspace_id: str, source_id: str, owner: str, fragments: tuple[Fragment, ...]
    ) -> None:
        result = self._index.index(workspace_id, owner, source_id, fragments)
        if result.skipped == "source_gone":
            self._check(workspace_id, source_id, owner)
        if result.skipped is not None:
            raise _Stop  # a newer version owns the source now
        self._progress(workspace_id, source_id, result.indexed)

    def _extract(
        self,
        workspace_id: str,
        source: Source,
        sections: tuple[Section, ...],
        fragments: tuple[Fragment, ...],
        previous: frozenset[str] | None,
        stored: tuple[int, str, int],
    ) -> None:
        version, parser, size = stored
        source_id, owner = source.id, source.owner_id
        if self._version is None:
            self._record(workspace_id, source, owner, version, parser, None, size)
            self._finish(workspace_id, source_id, owner, "ready", "extraction_unavailable")
            return
        if previous is None:
            todo, dropped = fragments, None
        else:
            # Carry forward: only sections holding a fragment the previous version did not have.
            changed = {f.section_id for f in fragments if f.id not in previous}
            todo = tuple(f for f in fragments if f.section_id in changed)
            dropped = frozenset(previous - {f.id for f in fragments})
        batches = _batches(todo)
        self._enter(workspace_id, source_id, "extracting", total=len(batches))
        known = self._graph.recent_decisions(workspace_id, owner, KNOWN_DECISIONS)
        locale = source.language or Locale.EN
        parts: list[Extraction] = []
        skipped = 0
        failure: ExtractionError | None = None
        for done, batch in enumerate(batches, start=1):
            self._check(workspace_id, source_id, owner)
            try:
                result = self._extractor.extract_document(sections, batch, locale, known)
            except ExtractionError as error:  # this batch only: the others may still answer
                failure, skipped = error, skipped + 1
            else:
                parts.append(result.extraction)
                skipped += result.skipped_windows
            self._progress(workspace_id, source_id, done)
        if failure is not None and not parts:
            raise failure  # nothing could be read: the queue retries, then the import fails
        merged = merge(parts) if len(parts) > 1 else (parts[0] if parts else Extraction())
        clean = validate_extraction(
            merged,
            frozenset(f.id for f in todo),
            {d.id: EntityType.DECISION for d in known},
        )
        ref = SourceRef(source_id, source.title, source.created_at, source.kind)
        self._graph.replace_source_evidence(
            workspace_id, owner, ref, clean, dropped_fragment_ids=dropped
        )
        self._check(workspace_id, source_id, owner)  # a delete during the write: erase again
        # A partial extraction is not recorded as current, so a retry extracts again.
        tag = self._version if not skipped else None
        self._record(workspace_id, source, owner, version, parser, tag, size)
        self._finish(
            workspace_id, source_id, owner, "ready", "extraction_partial" if skipped else None
        )
        logger.info(
            "import_ready fragments=%s extracted=%s batches=%s skipped=%s",
            len(fragments),
            len(todo),
            len(batches),
            skipped,
        )

    # ------------------------------------------------------------------------------ guards

    def _check(self, workspace_id: str, source_id: str, owner: str, *, cancel: bool = True):
        """The live source; a deleted one is erased and a cancelled import compensated (_Stop)."""
        try:
            source = self._sources.get_for_processing(workspace_id, source_id)
        except NotFoundError:
            self._gone(workspace_id, source_id, owner)
        if cancel and self._imports.is_cancel_requested(workspace_id, source_id):
            self._cancelled(workspace_id, source_id, owner)
        return source

    def _gone(self, workspace_id: str, source_id: str, owner: str) -> Any:
        """Deleted while importing: nothing searchable, no graph evidence, no file, no name."""
        self._erase_derived(workspace_id, source_id)
        self._blobs.erase(workspace_id, source_id)
        with suppress(NotFoundError):
            dead = self._sources.get_owned(workspace_id, owner, source_id, include_tombstoned=True)
            if dead.tombstoned_at is not None and (dead.title or dead.origin):
                self._sources.save(workspace_id, replace(dead, title=None, origin=None))
        self._imports.delete(workspace_id, source_id)
        invalidate(self._answers, workspace_id, owner)
        logger.info("import_stopped reason=deleted")
        raise _Stop

    def _cancelled(self, workspace_id: str, source_id: str, owner: str) -> Any:
        try:
            job = self._imports.get_for_processing(workspace_id, source_id)
            self._imports.update(
                workspace_id, source_id, state="cancelled", step=CLEAR, expected_state=ACTIVE
            )
        except (ConflictError, NotFoundError):
            raise _Stop from None  # ended meanwhile: whoever ended it compensated
        logger.info("import_stopped reason=cancelled")
        self._compensate(workspace_id, source_id, owner, job.step, "cancelled")
        raise _Stop

    def _compensate(
        self, workspace_id: str, source_id: str, owner: str, step: str | None, ended: str
    ) -> None:
        """After the job ended failed or cancelled: erase a new version this attempt stored, or
        keep the earlier good one (the source stays ready on it)."""
        wrote = step == NEW_VERSION
        if wrote:
            self._erase_derived(workspace_id, source_id)
        kept = not wrote and bool(self._fragments.latest(workspace_id, source_id))
        self._save_state(workspace_id, source_id, owner, "ready" if kept else ended)
        invalidate(self._answers, workspace_id, owner)

    def _mark_new_version(self, workspace_id: str, source_id: str) -> None:
        try:
            self._imports.update(workspace_id, source_id, step=NEW_VERSION, expected_state=ACTIVE)
        except (ConflictError, NotFoundError):
            raise _Stop from None

    def _erase_derived(self, workspace_id: str, source_id: str) -> None:
        """The source is the caller's own (checked through its job or owner) before this runs."""
        self._index.erase_source(workspace_id, source_id)  # the memory index has no trigger
        self._graph.erase(workspace_id, source_id)
        self._fragments.erase(workspace_id, source_id)

    # ------------------------------------------------------------------------------ helpers

    def _owned(self, workspace_id: str, user_id: str, source_id: str) -> Source:
        source = self._sources.get_owned(workspace_id, user_id, source_id)
        if source.kind is SourceKind.MEETING:
            raise NotFoundError
        return source

    def _fits(self, text: str) -> bool:
        if self._token_fits is not None:
            return self._token_fits([text])[0]
        return estimate_tokens(text) <= MAX_EMBEDDING_TOKENS

    def _publish(self, workspace_id: str, source_id: str, digest: str, attempt: int) -> None:
        self._jobs.publish_once(
            workspace_id,
            job_key(source_id, digest, attempt),
            IMPORT_SOURCE,
            {"source_id": source_id},
        )

    def _enter(self, workspace_id: str, source_id: str, state: str, total: int = 0) -> None:
        try:  # the step (new version marker) is kept: it decides what a failure erases
            self._imports.update(
                workspace_id,
                source_id,
                state=state,
                done=0,
                total=total,
                expected_state=ACTIVE,
            )
        except (ConflictError, NotFoundError):
            raise _Stop from None  # ended (cancelled or failed) meanwhile

    def _progress(self, workspace_id: str, source_id: str, done: int) -> None:
        try:
            self._imports.update(workspace_id, source_id, done=done, expected_state=ACTIVE)
        except (ConflictError, NotFoundError):
            raise _Stop from None

    def _finish(
        self, workspace_id: str, source_id: str, owner: str, state: str, code: str | None
    ) -> None:
        try:
            self._imports.update(
                workspace_id,
                source_id,
                state=state,
                step=CLEAR,
                error_code=code or CLEAR,
                expected_state=ACTIVE,
            )
        except (ConflictError, NotFoundError):
            raise _Stop from None
        self._save_state(workspace_id, source_id, owner, state)
        invalidate(self._answers, workspace_id, owner)

    def _save_state(self, workspace_id: str, source_id: str, owner: str, state: str) -> None:
        try:
            fresh = self._sources.get_for_processing(workspace_id, source_id)
            self._sources.save(workspace_id, replace(fresh, state=state), live_only=True)
        except NotFoundError:
            self._gone(workspace_id, source_id, owner)

    def _record(
        self,
        workspace_id: str,
        source: Source,
        owner: str,
        version: int,
        parser: str,
        extraction_version: str | None,
        size: int,
    ) -> None:
        assert source.content_hash is not None
        try:
            self._fragments.record_version(
                workspace_id,
                source.id,
                version,
                source.content_hash,
                parser,
                extraction_version,
                source.title,
                size,
            )
        except NotFoundError:
            self._gone(workspace_id, source.id, owner)

    def _persisted(self, workspace_id: str, owner: str, source: Source):
        """The stored version of these bytes, when a run already got that far, else None."""
        try:
            records = self._fragments.versions(workspace_id, owner, source.id)
        except NotFoundError:
            return None
        current = self._fragments.latest(workspace_id, source.id)
        if not records or not current:
            return None
        top = records[0]
        if top.content_hash != source.content_hash or current[0].version != top.version:
            return None
        sections = self._fragments.outline(workspace_id, source.id)
        return sections, current, top.version, top.parser, top.size_bytes

    def _previous_ids(self, workspace_id: str, owner: str, source_id: str) -> frozenset[str] | None:
        """Fragment ids of the version before this one, when their extraction is complete and
        current; None means extract everything again."""
        current = self._fragments.latest(workspace_id, source_id)
        if not current:
            return frozenset()
        records = self._fragments.versions(workspace_id, owner, source_id)
        if (
            not records
            or records[0].version != current[0].version  # stored but never recorded
            or records[0].extraction_version != self._version
        ):
            return None
        return frozenset(f.id for f in current)

    def _stale(self, workspace_id: str, user_id: str, source: Source, job: ImportJob) -> bool:
        """A ready import whose extraction is missing or older than the current model."""
        if job.state != "ready" or self._version is None:
            return False
        records = self._fragments.versions(workspace_id, user_id, source.id)
        return bool(records) and records[0].extraction_version != self._version

    def _rerun(self, workspace_id: str, source: Source, job: ImportJob) -> ImportJob:
        assert source.content_hash is not None
        attempt = job.attempt + 1
        job = self._imports.update(
            workspace_id,
            source.id,
            state="queued",
            step=CLEAR,
            done=0,
            total=0,
            attempt=attempt,
            error_code=CLEAR,
            cancel_requested=False,
            expected_state=job.state,
        )
        try:
            fresh = self._sources.get_for_processing(workspace_id, source.id)
            self._sources.save(workspace_id, replace(fresh, state="queued"), live_only=True)
            self._publish(workspace_id, source.id, source.content_hash, attempt)
        except Exception:
            # Not queued after all: end the job failed with a retryable code, never leave it
            # queued with no message (retry and reimport refuse an active job).
            with suppress(Exception):
                self._imports.update(
                    workspace_id,
                    source.id,
                    state="failed",
                    error_code="queue_unavailable",
                    expected_state="queued",
                )
                kept = bool(self._fragments.latest(workspace_id, source.id))
                fresh = self._sources.get_for_processing(workspace_id, source.id)
                state = "ready" if kept else "failed"
                self._sources.save(workspace_id, replace(fresh, state=state), live_only=True)
            raise
        return job

    def _unchanged(self, workspace_id: str, user_id: str, twin: Source) -> ImportJob:
        """The answer for bytes the owner already has: the live source's job (retried when it
        failed, was cancelled or has an extraction that is not current)."""
        twin = self._sources.get_owned(workspace_id, user_id, twin.id)  # NotFoundError if gone
        job = self._imports.get_owned(workspace_id, user_id, twin.id)
        if job.state in ENDED or self._stale(workspace_id, user_id, twin, job):
            return self._rerun(workspace_id, twin, job)
        return job

    def _discard(self, workspace_id: str, source: Source) -> None:
        """Remove a new source whose bytes another source already holds."""
        self._sources.save(
            workspace_id,
            replace(source, tombstoned_at=self._clock(), title=None, origin=None),
        )
        self._blobs.erase(workspace_id, source.id)
        self._imports.delete(workspace_id, source.id)

    def _file_versions(self, workspace_id: str, source_id: str) -> list[Path]:
        paths: list[Path] = []
        while (path := self._blobs.path_for(workspace_id, source_id, len(paths) + 1)).is_file():
            paths.append(path)
        return paths

    def _original(self, workspace_id: str, source_id: str, digest: str) -> tuple[Path, int] | None:
        """The stored file with these bytes, newest first (a copy whose hash was not taken, after
        a race, is skipped)."""
        for path in reversed(self._file_versions(workspace_id, source_id)):
            size, found = self._blobs.size_and_sha256(path)
            if found == digest:
                return path, size
        return None
