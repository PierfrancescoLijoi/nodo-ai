from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from nodo_api.domain.answer_cache import AnswerKey, CachedAnswer
from nodo_api.domain.documents import ParsedDocument
from nodo_api.domain.exports import Export, ExportState
from nodo_api.domain.graph import (
    Change,
    DocumentExtraction,
    Entity,
    EntityType,
    Extraction,
    GraphWriteResult,
    MeetingDigest,
    MeetingRef,
    Relation,
    ReviewStatus,
    SourceRef,
    Subgraph,
)
from nodo_api.domain.maintenance import MaintenanceMeeting, PendingJob
from nodo_api.domain.models import (
    Locale,
    Meeting,
    SpeechJob,
    SpeechPoll,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.domain.retention import AudioDue
from nodo_api.domain.source_files import UploadSession
from nodo_api.domain.sources import (
    Clear,
    Fragment,
    FragmentHit,
    ImportJob,
    IndexResult,
    Section,
    Source,
    SourceKind,
    SourceVersion,
)

# Every method takes workspace_id (and, where data belongs to a person, the user or the set of
# meeting ids that person may see) so that access control happens inside the store query,
# before anything is retrieved, never after.


class DocumentParser(Protocol):
    def parse(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
        declared_name: str | None = None,
    ) -> ParsedDocument: ...


class MeetingRepository(Protocol):
    def add(
        self, workspace_id: str, meeting: Meeting, idempotency_key: str, request_hash: str
    ) -> Meeting: ...

    def get_by_idempotency_key(
        self, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        """Return the live meeting and the request hash stored with the key, or None."""
        ...

    def get_owned(
        self, workspace_id: str, user_id: str, meeting_id: str, *, include_tombstoned: bool = False
    ) -> Meeting:
        """Return the meeting only if the user owns it, otherwise raise NotFoundError."""
        ...

    def owned_ids(self, workspace_id: str, user_id: str) -> frozenset[str]: ...

    def get_for_processing(self, workspace_id: str, meeting_id: str) -> Meeting:
        """System access for the ingestion worker, which acts for the owner and has no user."""
        ...

    def save(self, workspace_id: str, meeting: Meeting) -> Meeting: ...

    def list_owned(
        self, workspace_id: str, user_id: str, limit: int, cursor: tuple[datetime, str] | None
    ) -> tuple[Meeting, ...]:
        """Newest first. Pass the last (created_at, id) seen as `cursor` for the next page."""
        ...

    def all_for_workspace(self, workspace_id: str) -> tuple[Meeting, ...]: ...


class TranscriptRepository(Protocol):
    def add_version(
        self, workspace_id: str, meeting_id: str, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptSegment, ...]: ...

    def get_version(
        self, workspace_id: str, meeting_id: str, version: int
    ) -> tuple[TranscriptSegment, ...]: ...

    def latest(self, workspace_id: str, meeting_id: str) -> tuple[TranscriptSegment, ...]: ...

    def get_segment(
        self, workspace_id: str, meeting_id: str, segment_id: str
    ) -> TranscriptSegment | None: ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class SourceRepository(Protocol):
    """Everything put into the knowledge base (ADR-029). Meetings appear here with their meeting
    id, written by the meeting repository; this port writes every other kind."""

    def add(self, workspace_id: str, source: Source) -> Source:
        """Store a new source. A meeting source raises ValueError: meetings are not added here."""
        ...

    def get_owned(
        self, workspace_id: str, user_id: str, source_id: str, *, include_tombstoned: bool = False
    ) -> Source:
        """Return the source only if the user owns it, otherwise raise NotFoundError."""
        ...

    def get_for_processing(self, workspace_id: str, source_id: str) -> Source:
        """System access for the ingestion worker, which acts for the owner and has no user."""
        ...

    def save(self, workspace_id: str, source: Source) -> Source:
        """Update title, state, origin, language, hash and tombstone. Owner and version stay.
        Raises DuplicateContentError if another live source of the same owner has the hash."""
        ...

    def find_by_hash(self, workspace_id: str, user_id: str, content_hash: str) -> Source | None:
        """The owner's live source with this content hash, or None. Meetings never match."""
        ...

    def list_owned(
        self,
        workspace_id: str,
        user_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
        kinds: frozenset[SourceKind] | None = None,
    ) -> tuple[Source, ...]:
        """Newest first, never tombstoned. Pass the last (created_at, id) seen as `cursor`."""
        ...


class FragmentRepository(Protocol):
    """Sections and fragments of a source, versioned like transcripts. For a meeting source
    `latest` and `get_fragment` read the transcript (fragment id = segment id)."""

    def add_version(
        self,
        workspace_id: str,
        source_id: str,
        sections: tuple[Section, ...],
        fragments: tuple[Fragment, ...],
    ) -> int:
        """Store the next version in one transaction and return its number (1-based). The sections
        replace the source's outline. Raises ValueError for a meeting or inconsistent input and
        NotFoundError for an unknown source."""
        ...

    def latest(self, workspace_id: str, source_id: str) -> tuple[Fragment, ...]:
        """Fragments of the newest version in ordinal order; empty if there are none."""
        ...

    def get_fragment(
        self, workspace_id: str, source_id: str, fragment_id: str
    ) -> Fragment | None: ...

    def outline(self, workspace_id: str, source_id: str) -> tuple[Section, ...]:
        """Sections in reading order (parents before children, siblings by ordinal)."""
        ...

    def erase(self, workspace_id: str, source_id: str) -> None:
        """Delete every fragment, section and version record of the source. A meeting raises
        ValueError."""
        ...

    def record_version(
        self,
        workspace_id: str,
        source_id: str,
        version: int,
        content_hash: str,
        parser: str,
        extraction_version: str | None,
        original_name: str | None,
        size_bytes: int,
    ) -> None:
        """Note what a version was made from. Recording the same version again replaces the row.
        A version with no fragments (no text layer) can be recorded. Raises NotFoundError for an
        unknown or deleted source and ValueError for a meeting."""
        ...

    def versions(
        self, workspace_id: str, user_id: str, source_id: str
    ) -> tuple[SourceVersion, ...]:
        """Recorded versions, newest first. NotFoundError unless the user owns a live source."""
        ...


class ImportJobRepository(Protocol):
    """Progress of the import of one source, read by the person and written by the worker."""

    def create(self, workspace_id: str, owner_id: str, source_id: str) -> ImportJob:
        """Start the job for the owner's live, non-meeting source, in state queued. Calling it
        again returns the job as it stands. NotFoundError for any other source."""
        ...

    def get_owned(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """NotFoundError unless the user owns the job."""
        ...

    def get_for_processing(self, workspace_id: str, source_id: str) -> ImportJob:
        """System access for the worker."""
        ...

    def update(
        self,
        workspace_id: str,
        source_id: str,
        *,
        state: str | None = None,
        step: str | Clear | None = None,
        done: int | None = None,
        total: int | None = None,
        attempt: int | None = None,
        error_code: str | Clear | None = None,
        cancel_requested: bool | None = None,
        expected_state: str | Collection[str] | None = None,
    ) -> ImportJob:
        """Change the fields given. None leaves a field alone; CLEAR empties step or error_code
        (an empty string is refused). ValueError for an unknown state, a negative counter or an
        empty string. With expected_state (a state or several) the write happens only if the job
        is in one of them, tested and written together: otherwise nothing is written and
        ConflictError is raised. NotFoundError if there is no job."""
        ...

    def request_cancel(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """Ask the worker to stop. The worker sees the flag through `is_cancel_requested`."""
        ...

    def is_cancel_requested(self, workspace_id: str, source_id: str) -> bool: ...

    def delete(self, workspace_id: str, source_id: str) -> None:
        """Remove the job; no error if there is none."""
        ...


class BlobStore(Protocol):
    def create_upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]: ...

    def create_read_url(self, workspace_id: str, meeting_id: str) -> str:
        """Short-lived read-only URL of the finished audio, for the speech service."""
        ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...

    def put_export(self, workspace_id: str, name: str, data: bytes) -> str:
        """Store a finished export and return its blob name."""
        ...

    def get_export(self, workspace_id: str, name: str) -> bytes: ...


class SourceBlobStore(Protocol):
    """Original files of documents (Task 6), stored as <workspace>/<source>/<version>/original.

    The client file name is metadata and never a path part. Refusals raise UploadRejected (with a
    code: too_large, low_disk, size_mismatch, unknown_token, cancelled) or ValueError for an
    unsafe id, version or file name; none of their messages contains a name."""

    def create_upload_session(
        self,
        workspace_id: str,
        owner_id: str,
        source_id: str,
        filename: str,
        size_bytes: int,
        *,
        version: int = 1,
    ) -> UploadSession:
        """A one-time link for one file; refuses a size over the import cap or too little disk."""
        ...

    def open_upload(self, token: str) -> AbstractContextManager[Any]:
        """Writer with write(chunk), then size and sha256; the original appears only on success."""
        ...

    def path_for(self, workspace_id: str, source_id: str, version: int) -> Path: ...

    def adopt(self, workspace_id: str, source_id: str, version: int, src: Path) -> Path:
        """Copy a watched file into the store."""
        ...

    def size_and_sha256(self, path: Path) -> tuple[int, str]: ...

    def erase(self, workspace_id: str, source_id: str) -> None:
        """Remove every version and leftover of a source; no error if there are none."""
        ...


class JobQueue(Protocol):
    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str: ...

    def publish_later(
        self,
        workspace_id: str,
        job_key: str,
        job_type: str,
        payload: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        """Deliver after a delay. Unlike publish_once there is no record to reconcile: whatever
        schedules a message also keeps the state that tells it what the message is for."""
        ...


class JobControl(Protocol):
    """Engine-side control of queued jobs (local engine; Service Bus has its own)."""

    def release_claims(self) -> int:
        """Hand back jobs a crashed engine left claimed, without charging a delivery. Called once
        at engine start (engine.json guarantees one engine). Returns how many were released."""
        ...

    def release_claim(self, message: Any) -> bool:
        """On a graceful stop, put back the job still running and refund its delivery. Matches
        only that claim of an open job; a job settled or claimed again meanwhile is untouched."""
        ...

    def cancel(self, workspace_id: str, job_key: str, job_type: str) -> bool:
        """Mark an unclaimed open job failed with reason cancelled, so it is never delivered.
        False for a claimed, finished or unknown job: a running import is cancelled
        cooperatively through ImportJobRepository.request_cancel."""
        ...


class Retriever(Protocol):
    def index(
        self, workspace_id: str, owner_id: str, segments: Sequence[TranscriptSegment]
    ) -> None:
        """Add or replace segments. One call per transcript: adapters batch the embedding and the
        upload, a long meeting must not cost one round trip per sentence."""
        ...

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> tuple[TranscriptSegment, ...]:
        """Only segments of meetings this owner owns. The owner is a field of every indexed
        document, so the access check costs the same however many meetings there are."""
        ...

    def count(self, workspace_id: str, meeting_id: str) -> int:
        """How many documents the index holds for the meeting (index maintenance compares it with
        the transcript)."""
        ...

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


class FragmentIndex(Protocol):
    """Search over the fragments of documents and other non-meeting sources. It mirrors the
    fragments of one version of each source and is a projection: every row can be rebuilt from the
    fragment repository. Separate from the meeting Retriever, which it never touches."""

    def index(
        self, workspace_id: str, owner_id: str, source_id: str, fragments: Sequence[Fragment]
    ) -> IndexResult:
        """Make the source's rows exactly these fragments, in one step: every earlier row of the
        source goes, whoever owned it. An empty sequence clears the source. Nothing is written, and
        the earlier rows stay, when the source is gone (deleted, tombstoned, unknown) or the
        fragments are not its current version (non-meeting sources); the result says which. The
        check runs in the write itself, after any embedding, so a delete that arrives meanwhile
        wins. A fragment over the 512 token embedding ceiling is kept whole in the text index and
        its vector reads the first 512 tokens; it is counted in `truncated`. Raises ValueError, and
        changes nothing, for fragments of another source or repeated ids."""
        ...

    def erase_source(self, workspace_id: str, source_id: str) -> None: ...

    def count_source(self, workspace_id: str, source_id: str) -> int: ...

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        limit: int,
        *,
        source_ids: frozenset[str] | None = None,
    ) -> tuple[FragmentHit, ...]:
        """Best fragments first, only those of this owner. `source_ids` narrows the search (an
        empty set finds nothing); the owner check is part of the query, not applied afterwards."""
        ...


class GraphStore(Protocol):
    """The temporal knowledge graph, partitioned by workspace and owner (ADR-012).

    Every read is bounded: depth, node count and fan-out per node are explicit arguments, so no
    query can walk an unbounded neighbourhood however large the graph grows.
    """

    def upsert_extraction(
        self, workspace_id: str, owner_id: str, meeting: MeetingRef, extraction: Extraction
    ) -> GraphWriteResult:
        """Replace this meeting's contribution with `extraction`. Idempotent."""
        ...

    def replace_source_evidence(
        self,
        workspace_id: str,
        user_id: str,
        source: SourceRef,
        extraction: Extraction,
        *,
        dropped_fragment_ids: frozenset[str] | None = None,
    ) -> None:
        """Write what a non-meeting source says, in one atomic step.

        Drops the evidence of `source.id` whose fragment id is in `dropped_fragment_ids` (all of
        it when None), then writes `extraction` exactly as `upsert_extraction` does for a meeting,
        with a SOURCE node in place of the meeting node, and sweeps what lost its support.
        Evidence always names `source.id`; nothing of other sources or meetings is touched."""
        ...

    def find_entities(
        self,
        workspace_id: str,
        owner_id: str,
        terms: Sequence[str],
        limit: int,
        types: frozenset[EntityType] | None = None,
        *,
        meetings_only: bool = False,
    ) -> tuple[Entity, ...]:
        """`meetings_only` (on every read /v1 uses) is the view frozen clients get: relations a
        meeting evidences (never CHILD_OF) with only their meeting evidence, and entities a meeting
        mentions. It is applied before limits, fanout and seeds, so documents never displace
        meeting data. A store that holds no source data may ignore it."""
        ...

    def overview(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Entity, ...]: ...

    def recent_decisions(
        self, workspace_id: str, owner_id: str, limit: int
    ) -> tuple[Entity, ...]: ...

    def neighborhood(
        self,
        workspace_id: str,
        owner_id: str,
        seed_ids: Sequence[str],
        *,
        depth: int,
        node_limit: int,
        fanout: int,
        include_proposed: bool,
        as_of: datetime | None,
        meetings_only: bool = False,
    ) -> Subgraph: ...

    def review_relation(
        self,
        workspace_id: str,
        owner_id: str,
        relation_id: str,
        status: ReviewStatus,
        *,
        meetings_only: bool = False,
    ) -> Relation:
        """With `meetings_only`, a relation that view hides raises NotFoundError before anything
        is written."""
        ...

    def meeting_digests(
        self, workspace_id: str, owner_id: str, meeting_ids: Sequence[str]
    ) -> dict[str, MeetingDigest]: ...

    def recent_changes(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> tuple[Change, ...]: ...

    def pending_review(
        self, workspace_id: str, owner_id: str, limit: int, *, meetings_only: bool = False
    ) -> Subgraph:
        """The relations the model proposed and nobody has decided, newest first, with the entities
        at their ends and their evidence. `truncated` says there are more than `limit`."""
        ...

    def entities_missing_vectors(
        self, workspace_id: str, owner_id: str, limit: int, dimensions: int | None
    ) -> tuple[Entity, ...]:
        """Entities (never meeting nodes) with no vector or, when `dimensions` is given, with one
        of another size (the embedding model changed)."""
        ...

    def put_entity_vectors(
        self, workspace_id: str, owner_id: str, vectors: Mapping[str, Sequence[float]]
    ) -> None:
        """Store or replace vectors. Ids that are not entities of this owner are ignored."""
        ...

    def similar_entities(
        self,
        workspace_id: str,
        owner_id: str,
        vector: Sequence[float],
        limit: int,
        min_similarity: float,
    ) -> tuple[Entity, ...]:
        """Entities of this owner whose vector (of the same size) has cosine similarity of at
        least `min_similarity` with `vector`, nearest first."""
        ...

    def evidence_segment_ids(
        self, workspace_id: str, owner_id: str, meeting_id: str
    ) -> frozenset[str]:
        """The transcript segments the meeting's relations cite. Empty when the meeting left no
        evidence (or was erased); index maintenance checks that each one still exists."""
        ...

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        """Remove everything derived from the meeting; relations lose their evidence first."""
        ...


class Extractor(Protocol):
    def extract(
        self,
        segments: tuple[TranscriptSegment, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...],
        speakers: Mapping[str, str] | None = None,
    ) -> Extraction: ...


class FragmentExtractor(Protocol):
    """Entities and relations from fragments of one source; evidence ids are fragment ids and
    only ids of the fragments passed in. `section_path` is the breadcrumb of the window."""

    def extract(
        self,
        fragments: tuple[Fragment, ...],
        section_path: tuple[str, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> Extraction: ...


class DocumentExtractor(FragmentExtractor, Protocol):
    """A fragment extractor that also reads a whole document: windows by section, halves a window
    the model can not answer, and reports what it had to skip (never a silent partial result)."""

    def extract_document(
        self,
        sections: Sequence[Section],
        fragments: Sequence[Fragment],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> DocumentExtraction: ...


class LlmClient(Protocol):
    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[TranscriptSegment, ...],
        graph: Subgraph,
    ) -> Any: ...


class ExportStore(Protocol):
    def create(
        self, workspace_id: str, owner_id: str, meeting_id: str | None, now: datetime
    ) -> Export: ...

    def get(self, workspace_id: str, owner_id: str, export_id: str) -> Export:
        """Only the owner's export, otherwise NotFoundError."""
        ...

    def get_for_processing(self, workspace_id: str, export_id: str) -> Export: ...

    def set_state(
        self,
        workspace_id: str,
        export_id: str,
        state: ExportState,
        blob_name: str | None = None,
        size_bytes: int | None = None,
    ) -> None: ...


class RetentionStore(Protocol):
    """The one place that looks across workspaces, for the one job that must (ADR-019)."""

    def audio_due(self, now: datetime, limit: int) -> tuple[AudioDue, ...]:
        """Meetings whose audio has outlived its retention period and was not yet erased."""
        ...

    def mark_audio_erased(self, workspace_id: str, meeting_id: str, when: datetime) -> None: ...


class MaintenanceStore(Protocol):
    """The second place that looks across workspaces, for the one job that must (ADR-021)."""

    def list_meetings(
        self, after: tuple[str, str] | None, limit: int
    ) -> tuple[MaintenanceMeeting, ...]:
        """Every meeting, deleted ones included, ordered by (workspace id, meeting id). Pass the
        last pair of the previous page as `after`."""
        ...

    def unpublished_jobs(self, older_than: datetime, limit: int) -> tuple[PendingJob, ...]:
        """Jobs the outbox recorded before `older_than` and never sent."""
        ...

    def stuck_meetings(self, older_than: datetime, limit: int) -> tuple[MaintenanceMeeting, ...]:
        """Meetings still transcribing or extracting whose processing job began before
        `older_than`: longer than any legitimate run."""
        ...


class AuditLog(Protocol):
    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str: ...


class SpeechToText(Protocol):
    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]: ...


class StagedSpeechToText(Protocol):
    """The same service in three calls, so nobody has to wait inside one: submit returns at once
    with the provider's handle for the job, poll asks how it is going, cancel forgets it."""

    def submit(self, audio_url: str, candidate_locales: tuple[str, ...]) -> str: ...

    def poll(self, handle: str) -> SpeechPoll: ...

    def cancel(self, handle: str) -> None:
        """Best effort; the provider removes an abandoned job by itself after a few hours."""
        ...


class SpeechJobStore(Protocol):
    """The handle of the job a meeting is waiting for. Written before the next poll is scheduled,
    so any worker that receives the next message knows what to ask."""

    def get(self, workspace_id: str, meeting_id: str) -> SpeechJob | None: ...

    def start(self, workspace_id: str, meeting_id: str, handle: str, now: datetime) -> None: ...

    def record_poll(self, workspace_id: str, meeting_id: str) -> int:
        """Count one more poll and return the new total."""
        ...

    def clear(self, workspace_id: str, meeting_id: str) -> None: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AnswerStore(Protocol):
    """Answers already given, found again by the meaning of the question (ADR-026). Every method is
    scoped to one workspace and one owner; an owner's entries never serve another's questions."""

    def epoch(self, workspace_id: str, owner_id: str) -> int:
        """The owner's data version. It only grows; 0 until the first change."""
        ...

    def bump(self, workspace_id: str, owner_id: str) -> None:
        """The owner's knowledge changed: raise the epoch and drop every entry."""
        ...

    def nearest(
        self,
        workspace_id: str,
        owner_id: str,
        key: AnswerKey,
        vector: Sequence[float],
        min_similarity: float,
        fresh_after: datetime,
    ) -> CachedAnswer | None:
        """The closest entry of the current epoch with this version, locale and filters, a vector
        of the same size, cosine similarity of at least `min_similarity`, not older than
        `fresh_after`."""
        ...

    def put(
        self,
        workspace_id: str,
        owner_id: str,
        key: AnswerKey,
        vector: Sequence[float],
        answer: CachedAnswer,
        *,
        epoch: int,
        created_at: datetime,
        fresh_after: datetime,
        keep: int,
    ) -> bool:
        """Store (or replace the entry for the same key) only if `epoch` is still current; False
        when it is not. Also drops entries older than `fresh_after` and all but the newest
        `keep`."""
        ...
