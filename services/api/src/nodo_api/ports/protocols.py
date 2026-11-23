from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from nodo_api.domain.answer_cache import AnswerKey, CachedAnswer
from nodo_api.domain.documents import ParsedDocument
from nodo_api.domain.exports import Export, ExportState
from nodo_api.domain.graph import (
    Change,
    DocumentExtraction,
    Entity,
    EntityType,
    Evidence,
    Extraction,
    GraphWriteResult,
    MeetingDigest,
    MeetingRef,
    Relation,
    ReviewStatus,
    SourceRef,
    Subgraph,
)
from nodo_api.domain.hierarchy import CoMention, ParentSuggestion
from nodo_api.domain.maintenance import MaintenanceMeeting, PendingJob
from nodo_api.domain.models import (
    Locale,
    Meeting,
    SpeechJob,
    SpeechPoll,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.domain.pages import Page, PageProposal, PageRevision
from nodo_api.domain.retention import AudioDue
from nodo_api.domain.source_files import UploadSession
from nodo_api.domain.sources import (
    Clear,
    Fragment,
    FragmentHit,
    ImportJob,
    IndexResult,
    Section,
    SectionSummary,
    Source,
    SourceKind,
    SourceVersion,
    WatchFile,
    WatchFolder,
)
from nodo_api.domain.sources import (
    Collection as SourceCollection,
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

    def save(self, workspace_id: str, source: Source, *, live_only: bool = False) -> Source:
        """Update title, state, origin, language, hash and tombstone. Owner and version stay.
        Raises DuplicateContentError if another live source of the same owner has the hash. With
        live_only the write happens only while the stored source is not deleted, tested and written
        together (NotFoundError otherwise), so a late writer can not bring a deleted source back."""
        ...

    def update(
        self,
        workspace_id: str,
        source_id: str,
        *,
        state: str | None = None,
        content_hash: str | None = None,
        title: str | None = None,
        retention_until: datetime | Clear | None = None,
        expected_state: str | None = None,
    ) -> Source:
        """Write only the given columns of a live non-meeting source, so a concurrent edit of the
        others (title, language, origin) is never lost. CLEAR removes the retention date. With
        expected_state the write happens only
        while the stored state equals it (ConflictError otherwise). NotFoundError for a missing,
        deleted or meeting source; DuplicateContentError as for save."""
        ...

    def find_by_hash(self, workspace_id: str, user_id: str, content_hash: str) -> Source | None:
        """The owner's live source with this content hash, or None. Meetings never match."""
        ...

    def originals_due(self, now: datetime, limit: int) -> tuple[Source, ...]:
        """Uploaded live audio sources whose retention_until has passed, oldest first."""
        ...

    def claim_original_erase(self, source: Source) -> str | None:
        """Claim this exact source revision for retention. Return `erase`, `audit`, or None when
        another source change won. A persisted phase makes a crashed sweep resumable."""
        ...

    def prepare_original_erased(self, source: Source) -> bool:
        """Advance this exact claim from file erasure to its idempotent audit step."""
        ...

    def mark_original_erased(self, source: Source) -> bool:
        """After audit, clear retention and its claim while this exact live revision remains."""
        ...

    def unfinished_deletes(self, limit: int) -> tuple[Source, ...]:
        """Deleted non-meeting sources of every workspace whose erase has not finished (state
        `deleting`): at engine start the delete is run again for each."""
        ...

    def list_owned(
        self,
        workspace_id: str,
        user_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
        kinds: frozenset[SourceKind] | None = None,
        state: str | None = None,
        title_query: str | None = None,
    ) -> tuple[Source, ...]:
        """Newest first, never tombstoned. Optional state is exact and title query is a
        case-insensitive substring. Pass the last (created_at, id) seen as `cursor`."""
        ...

    def titles(
        self, workspace_id: str, user_id: str, source_ids: Sequence[str]
    ) -> dict[str, str | None]:
        """Current titles of the owner's live sources among `source_ids`, meetings included, in
        one read. Missing, deleted or other owners' ids are absent."""
        ...


class WatchFolderRepository(Protocol):
    """Watched folders (Task 15) and what each scan saw of their files. Local and memory only."""

    def add(self, folder: WatchFolder) -> WatchFolder:
        """ValueError when the owner already watches this path."""
        ...

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[WatchFolder, ...]: ...

    def list_enabled(self) -> tuple[WatchFolder, ...]:
        """Every enabled folder of every owner: for the scan, never for a request."""
        ...

    def delete_owned(self, workspace_id: str, owner_id: str, folder_id: str) -> None:
        """Forget the folder and its files (not the sources); NotFoundError if not the owner's."""
        ...

    def observe_file(self, folder_id: str, rel_path: str, size: int, mtime_ns: int) -> WatchFile:
        """Record one scan: seen_stable_count grows while size and mtime stay, else restarts at
        1. sha256 and source_id are kept."""
        ...

    def get_file(self, folder_id: str, rel_path: str) -> WatchFile | None: ...

    def mark_imported(
        self,
        folder_id: str,
        rel_path: str,
        sha256: str,
        source_id: str | None,
        linked_only: bool = False,
    ) -> None:
        """The bytes this file had when it was last handled and the source holding them;
        linked_only when that source belongs to another path or an upload. No error if gone."""
        ...

    def unlink_source(self, source_id: str) -> None:
        """The source was deleted: no watched file points at it any more. Each file keeps its
        hash, so the same bytes are not imported again while they stay in the folder."""
        ...

    def prune(self, folder_id: str, keep: frozenset[str]) -> None:
        """Forget the files of a folder that a complete scan did not see."""
        ...

    def reset_stable(self, folder_id: str, rel_path: str) -> None:
        """Handle the file again at the next stable scan (after a failure worth retrying)."""
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


class SectionSummaryRepository(Protocol):
    def get(
        self, workspace_id: str, source_id: str, section_id: str, source_version: int
    ) -> SectionSummary | None:
        """Return only a summary for that exact source version."""
        ...

    def put(self, workspace_id: str, summary: SectionSummary) -> None:
        """Replace a summary only while the source and section are current."""
        ...

    def erase_source(self, workspace_id: str, source_id: str) -> None: ...


class PageRepository(Protocol):
    """Authoritative, local Wiki Pages (ADR-031).

    These methods are deliberately separate from source and fragment repositories: an approved
    revision is user data, whereas a proposal is rebuildable. `redact_source` is part of the
    synchronous deletion path; an implementation must not defer it to a background worker.
    """

    def put_proposal(self, page: Page, proposal: PageProposal) -> None:
        """Store or replace an unapproved proposal. Implementations reject invalid state."""
        ...

    def get_owned(self, workspace_id: str, owner_id: str, page_id: str) -> Page:
        """Return metadata only when it belongs to the owner, otherwise raise NotFoundError."""
        ...

    def proposal(
        self, workspace_id: str, owner_id: str, page_id: str
    ) -> PageProposal | None:
        """Read an owner's pending proposal; approved revisions are never returned as a proposal."""
        ...

    def append_revision(self, workspace_id: str, owner_id: str, revision: PageRevision) -> Page:
        """Approve a proposal or append a human edit. Revision numbers must be contiguous."""
        ...

    def revisions(
        self, workspace_id: str, owner_id: str, page_id: str
    ) -> tuple[PageRevision, ...]:
        """Return current and historical revisions newest first."""
        ...

    def mark_stale_for_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        """Mark approved Pages whose claims cite a superseded source; return affected ids."""
        ...

    def redact_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        """Synchronously redact all dependent revision claims before source fragments are erased.

        A mapping failure must redact the whole revision. Returns Pages affected by the deletion.
        """
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
        expected_attempt: int | None = None,
    ) -> ImportJob:
        """Change the fields given. None leaves a field alone; CLEAR empties step or error_code
        (an empty string is refused). ValueError for an unknown state, a negative counter or an
        empty string. With expected_state (a state or several) the write happens only if the job
        is in one of them, tested and written together: otherwise nothing is written and
        ConflictError is raised. expected_attempt works the same way on the attempt number.
        NotFoundError if there is no job."""
        ...

    def request_cancel(self, workspace_id: str, user_id: str, source_id: str) -> ImportJob:
        """Ask the worker to stop. The worker sees the flag through `is_cancel_requested`."""
        ...

    def is_cancel_requested(self, workspace_id: str, source_id: str) -> bool: ...

    def in_progress(self, limit: int) -> tuple[ImportJob, ...]:
        """Jobs of every workspace that have not ended (queued, parsing, indexing, extracting),
        least recently updated first: at engine start these are imports a crash interrupted or
        whose queue message may never have been published."""
        ...

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

    def erase_originals(self, workspace_id: str, source_id: str) -> None:
        """Remove every stored version of a live source (retention, a cancel before processing)
        and keep it writable: a later reimport may store a new original. Safe to repeat."""
        ...

    def revoke_links(self, workspace_id: str, source_id: str) -> None:
        """End every open upload link of a source and refuse an upload still streaming."""
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

    def discard(self, workspace_id: str, job_type: str, key_prefix: str) -> int:
        """Remove every job of this type whose key starts with `key_prefix`, in any state. A
        worker still running one settles nothing afterwards. Returns how many went."""
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
        *,
        source_ids: frozenset[str] | None = None,
    ) -> tuple[TranscriptSegment, ...]:
        """Only segments of meetings this owner owns. The owner is a field of every indexed
        document, so the access check costs the same however many meetings there are. `source_ids`
        narrows the search to those meeting sources; an empty set returns no evidence."""
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

    def compact(self) -> None:
        """After a delete: make erased rows unrecoverable from the store files (SQLite: truncate
        the write-ahead log). Best effort: never raises, logs a code when it can not finish."""
        ...

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
        source_ids: frozenset[str] | None = None,
    ) -> tuple[Entity, ...]:
        """`meetings_only` (on every read /v1 uses) is the view frozen clients get: relations a
        meeting evidences (never CHILD_OF) with only their meeting evidence, and entities a meeting
        mentions. It is applied before limits, fanout and seeds, so documents never displace
        meeting data. `source_ids` filters relation evidence inside the store before seeds or
        limits are applied; an empty set produces no graph. A store that holds no source data may
        ignore the source filter."""
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
        source_ids: frozenset[str] | None = None,
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


class CollectionRepository(Protocol):
    """Owner-made nested collections of sources (FR-4, FR-8). Local and memory only. Names are
    checked with `collection_name` (ValueError that never repeats the name)."""

    def create(
        self, workspace_id: str, owner_id: str, *, name: str, parent_id: str | None
    ) -> SourceCollection:
        """NotFoundError for a parent that is not the owner's; DuplicateName for a sibling with the
        same normalized name; TooManyCollections past the limit; DepthRefused when the parent
        already has six ancestors. Every check and the write are one atomic step."""
        ...

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[SourceCollection, ...]:
        """Parents before children, siblings by normalized name, `source_count` of live sources."""
        ...

    def get_owned(
        self, workspace_id: str, owner_id: str, collection_id: str
    ) -> SourceCollection: ...

    def update(
        self,
        workspace_id: str,
        owner_id: str,
        collection_id: str,
        *,
        name: str | None = None,
        parent_id: str | Clear | None = None,
    ) -> SourceCollection:
        """None leaves a field, CLEAR moves to the root; a move is checked with check_link over
        the collection tree in the write (CycleRefused, DepthRefused), names as in create."""
        ...

    def delete(self, workspace_id: str, owner_id: str, collection_id: str) -> int:
        """The collection and its subtree with their memberships; returns how many collections
        went. Sources are never touched."""
        ...

    def add_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        """Idempotent. NotFoundError unless the collection and the live source are the owner's,
        checked together with the insert (a source tombstoned meanwhile is never added)."""
        ...

    def remove_source(
        self, workspace_id: str, owner_id: str, collection_id: str, source_id: str
    ) -> None:
        """Idempotent."""
        ...

    def list_sources(
        self,
        workspace_id: str,
        owner_id: str,
        collection_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[Source, ...]:
        """Live member sources, newest first; NotFoundError for a collection not the owner's."""
        ...

    def collections_of(self, workspace_id: str, owner_id: str, source_id: str) -> tuple[str, ...]:
        """Ids of the owner's collections that hold the source."""
        ...

    def forget_source(self, workspace_id: str, source_id: str) -> None:
        """Remove every membership of the source (erasure)."""
        ...


class HierarchyStore(Protocol):
    """Reads over the parent links between concepts (ADR-030). Only the memory and SQLite graph
    stores implement it; the Azure engine has none."""

    def hierarchy(
        self,
        workspace_id: str,
        owner_id: str,
        entity_id: str,
        *,
        direction: Literal["parents", "children", "both"],
        depth: int,
        include_proposed: bool,
        limit: int,
    ) -> Subgraph:
        """Walk CHILD_OF only (approved, plus proposed when asked, never rejected) from the seed,
        breadth first, at most `depth` levels (ValueError outside 1..MAX_DEPTH). Nodes are unique,
        seed first; every traversed edge comes once with its evidence; `truncated` when `limit`
        nodes were reached. NotFoundError when the entity is not the owner's or is an origin
        node."""
        ...

    def pending_hierarchy(self, workspace_id: str, owner_id: str, limit: int) -> Subgraph:
        """Proposed CHILD_OF relations, newest first, with their end entities and evidence."""
        ...

    def child_of(self, workspace_id: str, owner_id: str, relation_id: str) -> Relation:
        """The owner's CHILD_OF relation, else NotFoundError."""
        ...

    def mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[Entity, ...]:
        """The meeting and source nodes the entity is MENTIONED_IN, newest first."""
        ...

    def co_mentions(
        self, workspace_id: str, owner_id: str, entity_id: str, limit: int
    ) -> tuple[CoMention, ...]: ...

    def propose_child_of(
        self,
        workspace_id: str,
        owner_id: str,
        child_id: str,
        parent_id: str,
        confidence: float,
        evidence: tuple[Evidence, ...],
    ) -> Relation | None: ...


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


class ParentSuggester(Protocol):
    def suggest(
        self,
        concept: Entity,
        candidates: tuple[Entity, ...],
        fragments: tuple[Fragment, ...],
        locale: Locale,
    ) -> tuple[ParentSuggestion, ...]: ...


class LlmClient(Protocol):
    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[Fragment, ...],
        graph: Subgraph,
        summaries: tuple[SectionSummary, ...] = (),
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
    def append(
        self,
        workspace_id: str,
        event_type: str,
        metadata: dict[str, str],
        *,
        event_id: str | None = None,
    ) -> str:
        """Record one event and return its id. With `event_id` the append is idempotent: an event
        with that id already in the workspace is kept and nothing is written (a retried delete
        audits once). The local and memory logs take it; the Azure engine has no caller."""
        ...


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
