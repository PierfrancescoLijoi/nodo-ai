from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

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
from nodo_api.domain.models import Locale, Meeting, SpokenPhrase, TranscriptSegment

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


class JobQueue(Protocol):
    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str: ...


class Retriever(Protocol):
    def index(self, workspace_id: str, segment: TranscriptSegment) -> None: ...

    def search(
        self,
        workspace_id: str,
        query: str,
        filters: dict[str, Any] | None,
        allowed_meeting_ids: frozenset[str],
        limit: int,
    ) -> tuple[TranscriptSegment, ...]: ...

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


class AuditLog(Protocol):
    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str: ...


class SpeechToText(Protocol):
    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
