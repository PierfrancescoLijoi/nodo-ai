from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from nodo_api.domain.exports import Export, ExportState
from nodo_api.domain.graph import (
    Change,
    Entity,
    EntityType,
    Extraction,
    GraphWriteResult,
    MeetingDigest,
    MeetingRef,
    Relation,
    ReviewStatus,
    Subgraph,
)
from nodo_api.domain.models import (
    Locale,
    Meeting,
    SpeechJob,
    SpeechPoll,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.domain.retention import AudioDue

# Every method takes workspace_id (and, where data belongs to a person, the user or the set of
# meeting ids that person may see) so that access control happens inside the store query,
# before anything is retrieved, never after.


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

    def erase(self, workspace_id: str, meeting_id: str) -> None: ...


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

    def find_entities(
        self,
        workspace_id: str,
        owner_id: str,
        terms: Sequence[str],
        limit: int,
        types: frozenset[EntityType] | None = None,
    ) -> tuple[Entity, ...]: ...

    def overview(self, workspace_id: str, owner_id: str, limit: int) -> tuple[Entity, ...]: ...

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
    ) -> Subgraph: ...

    def review_relation(
        self, workspace_id: str, owner_id: str, relation_id: str, status: ReviewStatus
    ) -> Relation: ...

    def meeting_digests(
        self, workspace_id: str, owner_id: str, meeting_ids: Sequence[str]
    ) -> dict[str, MeetingDigest]: ...

    def recent_changes(
        self, workspace_id: str, owner_id: str, limit: int
    ) -> tuple[Change, ...]: ...

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
