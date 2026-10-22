from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

from nodo_api.domain.models import (
    Answerability,
    Citation,
    GraphNode,
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.errors import NotFoundError


class MemoryMeetingRepository:
    def __init__(self) -> None:
        self._meetings: dict[tuple[str, str], Meeting] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}

    def add(
        self, workspace_id: str, meeting: Meeting, idempotency_key: str, request_hash: str
    ) -> Meeting:
        existing = self.get_by_idempotency_key(workspace_id, meeting.owner_id, idempotency_key)
        if existing is not None:
            return existing[0]
        self._meetings[(workspace_id, meeting.id)] = meeting
        key = (workspace_id, meeting.owner_id, idempotency_key)
        self._idempotency[key] = (meeting.id, request_hash)
        return meeting

    def get_by_idempotency_key(
        self, workspace_id: str, user_id: str, key: str
    ) -> tuple[Meeting, str] | None:
        stored = self._idempotency.get((workspace_id, user_id, key))
        if stored is None:
            return None
        meeting = self._meetings.get((workspace_id, stored[0]))
        # A deleted meeting must not be handed back: the key starts a new meeting instead.
        if meeting is None or meeting.tombstoned_at is not None:
            return None
        return meeting, stored[1]

    def get_owned(
        self, workspace_id: str, user_id: str, meeting_id: str, *, include_tombstoned: bool = False
    ) -> Meeting:
        meeting = self._meetings.get((workspace_id, meeting_id))
        if meeting is None or meeting.owner_id != user_id:
            raise NotFoundError
        if meeting.tombstoned_at and not include_tombstoned:
            raise NotFoundError
        return meeting

    def get_for_processing(self, workspace_id: str, meeting_id: str) -> Meeting:
        meeting = self._meetings.get((workspace_id, meeting_id))
        if meeting is None or meeting.tombstoned_at is not None:
            raise NotFoundError
        return meeting

    def owned_ids(self, workspace_id: str, user_id: str) -> frozenset[str]:
        return frozenset(
            meeting.id
            for (stored_workspace, _), meeting in self._meetings.items()
            if stored_workspace == workspace_id
            and meeting.owner_id == user_id
            and meeting.tombstoned_at is None
        )

    def save(self, workspace_id: str, meeting: Meeting) -> Meeting:
        if (workspace_id, meeting.id) not in self._meetings:
            raise NotFoundError
        self._meetings[(workspace_id, meeting.id)] = meeting
        return meeting

    def all_for_workspace(self, workspace_id: str) -> tuple[Meeting, ...]:
        return tuple(
            meeting
            for (stored_workspace, _), meeting in self._meetings.items()
            if stored_workspace == workspace_id and meeting.tombstoned_at is None
        )


class MemoryTranscriptRepository:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str, int], tuple[TranscriptSegment, ...]] = {}

    def add_version(
        self, workspace_id: str, meeting_id: str, segments: tuple[TranscriptSegment, ...]
    ) -> tuple[TranscriptSegment, ...]:
        if not segments or any(segment.meeting_id != meeting_id for segment in segments):
            raise ValueError("A transcript version must contain segments for one meeting")
        key = (workspace_id, meeting_id, segments[0].version)
        if key in self._versions:
            raise ValueError("Transcript versions are immutable")
        self._versions[key] = segments
        return segments

    def get_version(
        self, workspace_id: str, meeting_id: str, version: int
    ) -> tuple[TranscriptSegment, ...]:
        try:
            return self._versions[(workspace_id, meeting_id, version)]
        except KeyError as exc:
            raise NotFoundError from exc

    def latest(self, workspace_id: str, meeting_id: str) -> tuple[TranscriptSegment, ...]:
        versions = [
            version
            for stored_workspace, stored_meeting, version in self._versions
            if stored_workspace == workspace_id and stored_meeting == meeting_id
        ]
        if not versions:
            raise NotFoundError
        return self.get_version(workspace_id, meeting_id, max(versions))

    def get_segment(
        self, workspace_id: str, meeting_id: str, segment_id: str
    ) -> TranscriptSegment | None:
        matching = [
            segment
            for (stored_workspace, stored_meeting, _), segments in self._versions.items()
            if stored_workspace == workspace_id and stored_meeting == meeting_id
            for segment in segments
            if segment.id == segment_id
        ]
        return max(matching, key=lambda segment: segment.version, default=None)

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        for key in list(self._versions):
            if key[:2] == (workspace_id, meeting_id):
                del self._versions[key]


class MemoryBlobStore:
    def __init__(self) -> None:
        self._erased: set[tuple[str, str]] = set()

    def create_upload_session(self, workspace_id: str, meeting_id: str) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "upload_url": f"memory://{workspace_id}/{meeting_id}",
            "expires_in_seconds": 900,
        }

    def create_read_url(self, workspace_id: str, meeting_id: str) -> str:
        return f"memory://{workspace_id}/{meeting_id}/audio"

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        self._erased.add((workspace_id, meeting_id))

    def was_erased(self, workspace_id: str, meeting_id: str) -> bool:
        return (workspace_id, meeting_id) in self._erased


class MemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str, str], str] = {}

    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str:
        key = (workspace_id, job_key, job_type)
        self._jobs.setdefault(key, str(uuid4()))
        return self._jobs[key]

    def publish_count(self, workspace_id: str, job_key: str, job_type: str) -> int:
        return int((workspace_id, job_key, job_type) in self._jobs)


class MockRetriever:
    def __init__(self) -> None:
        self._segments: dict[tuple[str, str, str], TranscriptSegment] = {}

    def index(self, workspace_id: str, segment: TranscriptSegment) -> None:
        self._segments[(workspace_id, segment.meeting_id, segment.id)] = segment

    def search(
        self,
        workspace_id: str,
        query: str,
        filters: dict[str, Any] | None,
        allowed_meeting_ids: frozenset[str],
        limit: int,
    ) -> tuple[TranscriptSegment, ...]:
        terms = {term for term in re.findall(r"\w+", query.lower()) if len(term) > 3}
        project = (filters or {}).get("project")
        results = []
        for (stored_workspace, meeting_id, _), segment in sorted(self._segments.items()):
            if stored_workspace != workspace_id or meeting_id not in allowed_meeting_ids:
                continue
            if project and segment.project != project:
                continue
            words = set(re.findall(r"\w+", segment.text.lower()))
            if terms & words or "ingestion" in query.lower():
                results.append(segment)
        return tuple(results[:limit])

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        for key in list(self._segments):
            if key[:2] == (workspace_id, meeting_id):
                del self._segments[key]


class MemoryGraphStore:
    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], GraphNode] = {}
        self.last_depth = 0
        self.last_node_limit = 0

    def add(self, workspace_id: str, node: GraphNode) -> None:
        self._nodes[(workspace_id, node.id)] = node

    def lookup(
        self,
        workspace_id: str,
        entity: str,
        depth: int,
        node_limit: int,
        allowed_meeting_ids: frozenset[str],
    ) -> tuple[GraphNode, ...]:
        self.last_depth = min(depth, 2)
        self.last_node_limit = node_limit
        return tuple(
            node
            for (stored_workspace, _), node in self._nodes.items()
            if stored_workspace == workspace_id
            and node.meeting_id in allowed_meeting_ids
            and entity.lower() in node.label.lower()
        )[:node_limit]

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        for key, node in list(self._nodes.items()):
            if key[0] == workspace_id and node.meeting_id == meeting_id:
                del self._nodes[key]


class MemoryAuditLog:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], tuple[str, dict[str, str]]] = {}

    def append(self, workspace_id: str, event_type: str, metadata: dict[str, str]) -> str:
        event_id = str(uuid4())
        self._events[(workspace_id, event_id)] = (event_type, dict(metadata))
        return event_id


class MockLlm:
    _ANSWERS: ClassVar[dict[Locale, str]] = {
        Locale.EN: "The ingestion pipeline moved from synchronous processing to an async queue.",
        Locale.PL: "Potok ingestion zmieniono z synchronicznego na asynchroniczną kolejkę.",
        Locale.IT: "La pipeline di ingestion è passata da sincrona a una coda asincrona.",
        Locale.DA: "Ingestion-pipelinen gik fra synkron behandling til en asynkron kø.",
    }

    def localized_answer(self, locale: Locale) -> str:
        return self._ANSWERS[locale]

    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[TranscriptSegment, ...],
        graph: tuple[GraphNode, ...],
    ):
        from nodo_api.services.query import GeneratedAnswer

        if not evidence:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        citations = tuple(
            Citation(item.meeting_id, item.id, item.start_ms, item.end_ms, item.text)
            for item in evidence[:3]
        )
        entities = tuple(dict.fromkeys(node.label for node in graph))
        return GeneratedAnswer(
            self.localized_answer(locale), Answerability.SUPPORTED, citations, entities
        )


def seed_memory_adapters(
    meetings: MemoryMeetingRepository,
    transcripts: MemoryTranscriptRepository,
    retriever: MockRetriever,
    graph: MemoryGraphStore,
) -> None:
    texts = (
        ("seed-m1", "seg-001", "Marco proposed keeping the ingestion pipeline synchronous."),
        ("seed-m2", "seg-002", "Sara benchmarked an async queue for the ingestion pipeline."),
        ("seed-m3", "seg-003", "Giulia confirmed the async queue absorbs ingestion traffic peaks."),
    )
    for index, (meeting_id, segment_id, text) in enumerate(texts, start=1):
        attestation = _seed_attestation(meeting_id)
        meeting = Meeting(
            meeting_id,
            "workspace-a",
            "user-a",
            datetime(2026, 10, index, tzinfo=UTC),
            MeetingState.READY,
            attestation,
            "NODO",
        )
        meetings.add("workspace-a", meeting, f"seed-{index}", "seed")
        segment = TranscriptSegment(
            segment_id,
            meeting_id,
            f"speaker-{index}",
            index * 1000,
            index * 1000 + 800,
            text,
            0.99,
            1,
            Locale.EN,
            "NODO",
        )
        transcripts.add_version("workspace-a", meeting_id, (segment,))
        retriever.index("workspace-a", segment)
    graph.add("workspace-a", GraphNode("node-1", "ingestion", "seed-m1", "seg-001"))
    graph.add("workspace-a", GraphNode("node-2", "async-queue", "seed-m1", "seg-001"))


def _seed_attestation(meeting_id: str) -> RecordingAttestation:
    return RecordingAttestation(
        "user-a",
        meeting_id,
        "seed-v1",
        datetime(2026, 10, 1, tzinfo=UTC),
        "retention-30d",
        Locale.EN,
        f"audit-{meeting_id}",
        "seed-signature",
    )


class MemorySpeech:
    """Deterministic stand-in for speech to text: two speakers, three phrases."""

    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]:
        locale = candidate_locales[0] if candidate_locales else "en-GB"
        return (
            SpokenPhrase(1, 0, 2400, "We start with the ingestion queue.", 0.97, locale),
            SpokenPhrase(2, 2500, 5200, "Sara will benchmark the retries.", 0.95, locale),
            SpokenPhrase(1, 5300, 7000, "Agreed, deadline is the twenty-eighth.", 0.96, locale),
        )
