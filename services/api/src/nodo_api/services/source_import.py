"""Source import: turn an uploaded or watched file into a versioned Source with Sections, Fragments,
a search index and graph evidence (FR-1, FR-2, FR-3; ADR-029).

It mirrors the ingestion service. Every stage is idempotent and resumable: a retry reuses the
version that was already stored, so a model failure never costs a new parse. Deletion and cancel
win at every step: after each stage, and before each batch of extraction windows, the source and the
cancel flag are read again, and a deleted or cancelled import erases what it wrote (index, graph,
fragments; for a deletion also the original file and the file name). Job state changes are
compare-and-set, so a late worker write never turns a cancelled or failed job back.

Nothing here logs or audits document text, a title or a file name: logs carry states, codes and
counts, the audit carries the kind.

Audio and video (FR-5) wait in `awaiting_confirmation` until the owner confirms "I may process
this recording" (`confirm`); every new or retried recording asks again. They are transcribed with
the meeting speech pipeline (SpeechToText, build_segments) into timed fragments under one root
section, then indexed and extracted like a document. Their stored original is erased 30 days after
the upload by the retention sweep (fragments stay); a cancel before confirmation erases it at
once."""

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
from nodo_api.domain.retention import audio_expires_at
from nodo_api.domain.source_files import looks_like_media, safe_filename
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
from nodo_api.errors import ConflictError, NotFoundError, OriginalMissing, ValidationFailed
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
    SpeechToText,
)
from nodo_api.services.answer_cache import AnswerCache, invalidate
from nodo_api.services.extraction import merge
from nodo_api.services.ingestion import build_segments

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
AWAITING = "awaiting_confirmation"
# Job step of a recording whose new file is being copied by a reimport: it waits (never active),
# and confirm and cancel answer 409 until the copy is done. A crash leaves it waiting; importing
# the file again clears it.
REIMPORTING = "reimporting"
# What the owner of an imported recording confirms (FR-5). The audit event stands for it; neither
# the text nor anything about the recording is written into the event.
CONFIRMATION_TEXT = "I may process this recording"
AUDIO_RETENTION_POLICY = "retention-30d"  # the meeting default (ADR-019)
TRANSCRIPT_SECTION = "Transcript"
_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".mkv", ".mov", ".webm")
# Meetings are created by the meeting service.
_KINDS = {
    **dict.fromkeys(_AUDIO_EXTENSIONS, SourceKind.AUDIO),
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


def _kind_for(name: str, kind: SourceKind | None, *, audio: bool) -> SourceKind:
    """The kind of a file name. Audio is decided by the extension both ways, and only an engine
    that can transcribe (`audio`) takes it."""
    inferred = _KINDS.get(Path(name).suffix.lower())
    is_audio = inferred is SourceKind.AUDIO
    if inferred is None or kind is SourceKind.MEETING or (is_audio and not audio):
        raise ValidationFailed
    if kind is not None and (kind is SourceKind.AUDIO) != is_audio:
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
        speech: SpeechToText | None = None,
        speech_locales: tuple[str, ...] = (),
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
        # The meeting speech engine; None: audio and video are refused at create.
        self._speech = speech
        self._speech_locales = speech_locales
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
        kind = _kind_for(name, kind, audio=self._speech is not None)
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
        created_at = self._clock()
        source = Source(
            source_id,
            workspace_id,
            user_id,
            kind,
            name,
            "queued",
            created_at,
            origin_note,
            retention_until=self._retention(kind, created_at),
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
            if (
                source.content_hash is not None
                or self._blobs.path_for(workspace_id, source_id, 1).is_file()
            ):
                raise ConflictError from None  # uploaded already: no new link for version 1
            with suppress(NotFoundError):  # no job yet: created below
                if self._imports.get_owned(workspace_id, user_id, source_id).state not in ACTIVE:
                    raise ConflictError from None  # cancelled: no link writes for it again
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
        # No other link (an idempotent replay) may write once the bytes are hashed; the store
        # also never replaces a stored original.
        self._blobs.revoke_links(workspace_id, source_id)
        _, digest = self._blobs.size_and_sha256(original)
        audio = source.kind is SourceKind.AUDIO
        # Not a recording (16 bytes, no decoder): refused before the owner is asked to confirm.
        refused = audio and not looks_like_media(original)
        for _ in range(3):  # each turn follows a race: a twin that appears or one that vanishes
            twin = self._sources.find_by_hash(workspace_id, user_id, digest)
            if twin is None:
                if audio:  # before the hash: a crash never leaves it queued unconfirmed
                    with suppress(ConflictError):  # cancelled meanwhile: it stays cancelled
                        self._imports.update(
                            workspace_id,
                            source_id,
                            state="failed" if refused else AWAITING,
                            error_code="unsupported" if refused else None,
                            expected_state=("queued", AWAITING),
                        )
                try:  # the hash column only: a cancel or an edit meanwhile is kept
                    source = self._sources.update(workspace_id, source_id, content_hash=digest)
                except NotFoundError:
                    self._blobs.erase(workspace_id, source_id)  # deleted meanwhile
                    raise
                except DuplicateContentError:
                    continue  # the same bytes completed at the same moment: answer with them
                if refused:
                    with suppress(ConflictError):
                        self._sources.update(
                            workspace_id, source_id, state="failed", expected_state="queued"
                        )
                job = self._imports.get_owned(workspace_id, user_id, source_id)
                if not audio:  # a recording is published by confirm
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
        audio = source.kind is SourceKind.AUDIO
        if (_kind_for(name, None, audio=self._speech is not None) is SourceKind.AUDIO) != audio:
            raise ValidationFailed  # a recording stays a recording, a document a document
        if audio and not looks_like_media(path):
            raise ValidationFailed  # not a recording: refused before anyone is asked
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        if job.state in ACTIVE:
            raise ConflictError  # one import of a source at a time
        _, digest = self._blobs.size_and_sha256(path)
        # The same recording after retention erased its original is stored again.
        restore = audio and not self._file_versions(workspace_id, source_id)
        if digest == source.content_hash and not restore:
            # A reimport that crashed after storing these bytes left its reservation: clear it.
            stale = job.step == REIMPORTING or self._stale(workspace_id, user_id, source, job)
            return self._rerun(workspace_id, source, job) if stale else job
        twin = self._sources.find_by_hash(workspace_id, user_id, digest)
        if twin is not None and twin.id != source_id:
            return self._unchanged(workspace_id, user_id, twin)
        # Reserve the source first: a second reimport now gets ConflictError, so two copies
        # never race for the same file version. A recording is reserved waiting, never active:
        # a replayed complete, the start sweep or a stray message cannot process it before the
        # owner confirms the new file; the attempt number is the compare-and-set token.
        previous_source = source
        reserved = self._imports.update(
            workspace_id,
            source_id,
            state=AWAITING if audio else "queued",
            step=REIMPORTING if audio else None,
            attempt=job.attempt + 1 if audio else None,
            expected_state=job.state,
            expected_attempt=job.attempt,
        )
        try:
            # Move the source revision before copying. The retention repository makes this update
            # and its erase claim mutually exclusive, so an expired old original can never erase
            # the new one after it is adopted.
            source = self._sources.update(
                workspace_id,
                source_id,
                state="queued",
                retention_until=self._retention(source.kind, self._clock()),
                expected_state=source.state,
            )
            version = len(self._file_versions(workspace_id, source_id)) + 1
            stored = self._blobs.adopt(workspace_id, source_id, version, path)
            _, digest = self._blobs.size_and_sha256(stored)  # what was copied is what counts
            source = self._sources.update(
                workspace_id,
                source_id,
                content_hash=digest,
                title=name,
                state="queued",
                retention_until=self._retention(source.kind, self._clock()),
            )
        except NotFoundError:
            self._blobs.erase(workspace_id, source_id)  # deleted during the copy
            raise
        except BaseException as error:
            with suppress(ConflictError, NotFoundError):
                self._sources.update(
                    workspace_id,
                    source_id,
                    state=previous_source.state,
                    retention_until=previous_source.retention_until or CLEAR,
                    expected_state="queued",
                )
            with suppress(ConflictError, NotFoundError):  # give the source back as it was
                self._imports.update(
                    workspace_id,
                    source_id,
                    state=job.state,
                    step=CLEAR if audio else None,
                    expected_state=reserved.state,
                    expected_attempt=reserved.attempt,
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
        if job.step == REIMPORTING:
            raise ConflictError  # a new file is being copied in: cancel after it
        if job.state == AWAITING:
            return self._cancel_unconfirmed(workspace_id, user_id, source)
        if job.state not in ACTIVE:
            raise ConflictError
        self._blobs.revoke_links(workspace_id, source_id)  # a later PUT writes nothing
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

    def _cancel_unconfirmed(self, workspace_id: str, user_id: str, source: Source) -> ImportJob:
        """Nothing was processed: end cancelled and erase the original at once (no consent)."""
        if source.state != "cancelling":
            self._sources.update(
                workspace_id, source.id, state="cancelling", expected_state=source.state
            )
        self._blobs.revoke_links(workspace_id, source.id)
        self._blobs.erase_originals(workspace_id, source.id)
        try:
            self._imports.update(
                workspace_id, source.id, state="cancelled", step=CLEAR, expected_state=AWAITING
            )
        except ConflictError:
            with suppress(ConflictError, NotFoundError):
                self._sources.update(
                    workspace_id, source.id, state=source.state, expected_state="cancelling"
                )
            return self.cancel(workspace_id, user_id, source.id)  # confirmed meanwhile
        self._sources.update(
            workspace_id,
            source.id,
            state="cancelled",
            retention_until=CLEAR,
            expected_state="cancelling",
        )
        invalidate(self._answers, workspace_id, user_id)
        self._audit.append(workspace_id, "source_import_cancelled", {"kind": source.kind.value})
        return self._imports.get_owned(workspace_id, user_id, source.id)

    def confirm(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """The owner confirms CONFIRMATION_TEXT for a recording awaiting it: audit (kind only,
        once per attempt) and queue the transcription. A recording already confirmed answers its
        current state; a cancelled or failed one, or a document, is a conflict."""
        source = self._owned(workspace_id, user_id, source_id)
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        if source.kind is not SourceKind.AUDIO or source.state == "cancelling":
            raise ConflictError
        if job.state == "failed" and job.error_code == "unsupported":
            return job  # refused as not a recording: there is nothing to confirm
        if job.state in ENDED or job.step == REIMPORTING:
            raise ConflictError  # ended, or a new file is still being copied in
        if job.state != AWAITING:
            return job  # confirmed already
        if self._original_gone(workspace_id, user_id, source):
            raise OriginalMissing
        # Keyed by the attempt: two confirmations at the same moment write one event, and the
        # event exists before any processing starts.
        event = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"nodo:source_processing_confirmed:{workspace_id}:{source_id}:{job.attempt}",
        )
        self._audit.append(
            workspace_id,
            "source_processing_confirmed",
            {"kind": source.kind.value},
            event_id=str(event),
        )
        try:
            return self._rerun(workspace_id, source, job, confirmed=True)
        except ConflictError:
            job = self._imports.get_owned(workspace_id, user_id, source_id)
            if job.state == AWAITING or job.state in ENDED:
                raise
            return job  # the other confirmation won

    def retry(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """Queue a failed or cancelled import again (also a ready one whose extraction is not
        current). The stored original is parsed again unless its version is already stored."""
        source = self._owned(workspace_id, user_id, source_id)
        job = self._imports.get_owned(workspace_id, user_id, source_id)
        allowed = job.state in ENDED or self._stale(workspace_id, user_id, source, job)
        if source.content_hash is None or not allowed:
            raise ConflictError
        if self._original_gone(workspace_id, user_id, source):
            raise OriginalMissing
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
            if source.kind is SourceKind.AUDIO:
                parsed = self._transcribe(workspace_id, source_id, owner, source)
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

    def _transcribe(self, workspace_id: str, source_id: str, owner: str, source: Source):
        """(sections, fragments, parser, size) from the meeting speech pipeline, or None when the
        import already ended. The engine transcribes the whole file in one call, so a delete or
        cancel during it is honoured right after it, before anything is written."""
        assert source.content_hash is not None
        self._enter(workspace_id, source_id, "parsing")
        found = self._original(workspace_id, source_id, source.content_hash)
        code = "original_missing" if found is None else None
        if found is not None and self._speech is None:
            code = "speech_unavailable"
        elif found is not None and not looks_like_media(found[0]):
            code = "unsupported"  # sniffed by content, whatever the extension says
        phrases = ()
        if code is None:
            assert found is not None and self._speech is not None
            try:
                phrases = self._speech.transcribe(str(found[0]), self._speech_locales)
            except ValueError:  # the recording could not be decoded: retrying will not help
                code = "damaged"
        self._check(workspace_id, source_id, owner)
        locale = source.language or Locale.EN
        segments = [s for s in build_segments(source_id, phrases, locale, None) if s.text.strip()]
        if code is None and not segments:
            code = "no_speech"
        if code is not None:
            self.mark_failed(workspace_id, source_id, code)
            return None
        assert found is not None
        root = Section("s_transcript", source_id, None, 0, 1, TRANSCRIPT_SECTION)
        fragments = tuple(
            replace(Fragment.from_segment(segment, ordinal), section_id=root.id)
            for ordinal, segment in enumerate(segments)
        )
        return (root,), fragments, "speech", found[1]

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

    @staticmethod
    def _retention(kind: SourceKind, stored_at: datetime) -> datetime | None:
        """When a stored original is erased: recordings after the meeting default, else never."""
        if kind is SourceKind.AUDIO:
            return audio_expires_at(stored_at, AUDIO_RETENTION_POLICY)
        return None

    def _original_gone(self, workspace_id: str, owner: str, source: Source) -> bool:
        """No stored file left and no stored version of these bytes to resume from."""
        return (
            not self._file_versions(workspace_id, source.id)
            and self._persisted(workspace_id, owner, source) is None
        )

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
        """Mirror an ended job on the source. Only from queued (the state of every active
        import): another writer that already moved it on is not overwritten."""
        try:
            self._sources.update(workspace_id, source_id, state=state, expected_state="queued")
        except ConflictError:
            logger.info("source_state_kept state=%s", state)
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

    def _rerun(
        self, workspace_id: str, source: Source, job: ImportJob, *, confirmed: bool = False
    ) -> ImportJob:
        """Queue the import again; a recording waits for a new confirmation unless `confirmed`."""
        assert source.content_hash is not None
        attempt = job.attempt + 1
        wait = source.kind is SourceKind.AUDIO and not confirmed
        job = self._imports.update(
            workspace_id,
            source.id,
            state=AWAITING if wait else "queued",
            step=CLEAR,
            done=0,
            total=0,
            attempt=attempt,
            error_code=CLEAR,
            cancel_requested=False,
            expected_state=job.state,
            expected_attempt=job.attempt,
        )
        if wait:
            return job
        try:
            self._sources.update(workspace_id, source.id, state="queued")
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
                self._sources.update(
                    workspace_id,
                    source.id,
                    state="ready" if kept else "failed",
                    expected_state="queued",
                )
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
