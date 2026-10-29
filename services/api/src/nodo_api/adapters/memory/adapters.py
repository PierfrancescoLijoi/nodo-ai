from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

from nodo_api.domain.exports import EXPORT_TTL_HOURS, Export, ExportState
from nodo_api.domain.graph import EntityType, Subgraph
from nodo_api.domain.maintenance import MaintenanceMeeting, PendingJob
from nodo_api.domain.models import (
    Answerability,
    Citation,
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    SpeechJob,
    SpokenPhrase,
    TranscriptSegment,
)
from nodo_api.domain.retention import AudioDue, audio_expires_at
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

    def list_owned(
        self, workspace_id: str, user_id: str, limit: int, cursor: tuple[datetime, str] | None
    ) -> tuple[Meeting, ...]:
        owned = [
            meeting
            for (stored_workspace, _), meeting in self._meetings.items()
            if stored_workspace == workspace_id
            and meeting.owner_id == user_id
            and meeting.tombstoned_at is None
        ]
        owned.sort(key=lambda m: (m.created_at, m.id), reverse=True)
        if cursor is not None:
            owned = [m for m in owned if (m.created_at, m.id) < cursor]
        return tuple(owned[:limit])

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
        self._exports: dict[tuple[str, str], bytes] = {}

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

    def put_export(self, workspace_id: str, name: str, data: bytes) -> str:
        self._exports[(workspace_id, name)] = data
        return name

    def get_export(self, workspace_id: str, name: str) -> bytes:
        try:
            return self._exports[(workspace_id, name)]
        except KeyError:
            raise NotFoundError from None


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    workspace_id: str
    job_key: str
    job_type: str
    payload: dict[str, Any]
    delay_seconds: int


class MemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str, str], str] = {}
        self.scheduled: list[ScheduledJob] = []
        self._created: dict[tuple[str, str, str], datetime] = {}
        self.unsent: list[tuple[PendingJob, datetime]] = []

    def backdate(self, workspace_id: str, job_key: str, job_type: str, when: datetime) -> None:
        self._created[(workspace_id, job_key, job_type)] = when

    def record_unsent(
        self,
        workspace_id: str,
        job_key: str,
        job_type: str,
        payload: dict[str, str],
        when: datetime,
    ) -> None:
        self.unsent.append((PendingJob(workspace_id, job_key, job_type, payload), when))

    def created_at(self, workspace_id: str, job_key: str, job_type: str) -> datetime | None:
        return self._created.get((workspace_id, job_key, job_type))

    def job_keys(self) -> list[tuple[str, str, str]]:
        return list(self._jobs)

    def publish_later(
        self,
        workspace_id: str,
        job_key: str,
        job_type: str,
        payload: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        self.scheduled.append(ScheduledJob(workspace_id, job_key, job_type, payload, delay_seconds))

    def publish_once(
        self, workspace_id: str, job_key: str, job_type: str, payload: dict[str, Any]
    ) -> str:
        key = (workspace_id, job_key, job_type)
        self._jobs.setdefault(key, str(uuid4()))
        self._created.setdefault(key, datetime.now(UTC))
        return self._jobs[key]

    def publish_count(self, workspace_id: str, job_key: str, job_type: str) -> int:
        return int((workspace_id, job_key, job_type) in self._jobs)


class MockRetriever:
    def __init__(self) -> None:
        self._segments: dict[tuple[str, str, str], TranscriptSegment] = {}
        self._owners: dict[tuple[str, str], str] = {}

    def index(
        self, workspace_id: str, owner_id: str, segments: Sequence[TranscriptSegment]
    ) -> None:
        for segment in segments:
            self._segments[(workspace_id, segment.meeting_id, segment.id)] = segment
            self._owners[(workspace_id, segment.meeting_id)] = owner_id

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> tuple[TranscriptSegment, ...]:
        terms = {term for term in re.findall(r"\w+", query.lower()) if len(term) > 3}
        project = (filters or {}).get("project")
        results = []
        for (stored_workspace, meeting_id, _), segment in sorted(self._segments.items()):
            if (
                stored_workspace != workspace_id
                or self._owners.get((workspace_id, meeting_id)) != owner_id
            ):
                continue
            if project and segment.project != project:
                continue
            words = set(re.findall(r"\w+", segment.text.lower()))
            if terms & words or "ingestion" in query.lower():
                results.append(segment)
        return tuple(results[:limit])

    def count(self, workspace_id: str, meeting_id: str) -> int:
        return sum(1 for (w, m, _) in self._segments if (w, m) == (workspace_id, meeting_id))

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        self._owners.pop((workspace_id, meeting_id), None)
        for key in list(self._segments):
            if key[:2] == (workspace_id, meeting_id):
                del self._segments[key]


class MemoryMaintenanceStore:
    """Every meeting, deleted ones included, for index maintenance over the in-memory repository."""

    def __init__(
        self, meetings: MemoryMeetingRepository, jobs: MemoryJobQueue | None = None
    ) -> None:
        self._meetings = meetings
        self._jobs = jobs

    def unpublished_jobs(self, older_than: datetime, limit: int) -> tuple[PendingJob, ...]:
        if self._jobs is None:
            return ()
        return tuple(job for job, when in self._jobs.unsent if when < older_than)[:limit]

    def stuck_meetings(self, older_than: datetime, limit: int) -> tuple[MaintenanceMeeting, ...]:
        if self._jobs is None:
            return ()
        found = []
        for (workspace_id, meeting_id), m in self._meetings._meetings.items():
            started = self._jobs.created_at(workspace_id, meeting_id, "transcribe")
            if (
                m.tombstoned_at is None
                and m.state in (MeetingState.TRANSCRIBING, MeetingState.EXTRACTING)
                and started is not None
                and started < older_than
            ):
                found.append(MaintenanceMeeting(workspace_id, m.id, m.owner_id, m.state, False))
        return tuple(found[:limit])

    def list_meetings(
        self, after: tuple[str, str] | None, limit: int
    ) -> tuple[MaintenanceMeeting, ...]:
        keys = sorted(k for k in self._meetings._meetings if after is None or k > after)
        return tuple(
            MaintenanceMeeting(
                m.workspace_id if hasattr(m, "workspace_id") else k[0],
                m.id,
                m.owner_id,
                m.state,
                m.tombstoned_at is not None,
            )
            for k in keys[:limit]
            for m in [self._meetings._meetings[k]]
        )


class MemoryExportStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Export] = {}

    def create(
        self, workspace_id: str, owner_id: str, meeting_id: str | None, now: datetime
    ) -> Export:
        item = Export(
            str(uuid4()),
            workspace_id,
            owner_id,
            meeting_id,
            ExportState.QUEUED,
            now,
            now + timedelta(hours=EXPORT_TTL_HOURS),
        )
        self._items[(workspace_id, item.id)] = item
        return item

    def get(self, workspace_id: str, owner_id: str, export_id: str) -> Export:
        item = self._items.get((workspace_id, export_id))
        if item is None or item.owner_id != owner_id:
            raise NotFoundError
        return item

    def get_for_processing(self, workspace_id: str, export_id: str) -> Export:
        item = self._items.get((workspace_id, export_id))
        if item is None:
            raise NotFoundError
        return item

    def set_state(
        self,
        workspace_id: str,
        export_id: str,
        state: ExportState,
        blob_name: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        item = self._items[(workspace_id, export_id)]
        self._items[(workspace_id, export_id)] = replace(
            item,
            state=state,
            blob_name=blob_name or item.blob_name,
            size_bytes=size_bytes if size_bytes is not None else item.size_bytes,
        )


class MemoryRetentionStore:
    """Retention over the in-memory meetings, for tests and the demo."""

    def __init__(self, meetings: MemoryMeetingRepository) -> None:
        self._meetings = meetings
        self._erased: set[tuple[str, str]] = set()

    def audio_due(self, now: datetime, limit: int) -> tuple[AudioDue, ...]:
        due = [
            AudioDue(w, m.id, m.owner_id, m.created_at, m.attestation.retention_policy_id)
            for (w, _), m in self._meetings._meetings.items()
            if m.tombstoned_at is None
            and (w, m.id) not in self._erased
            and audio_expires_at(m.created_at, m.attestation.retention_policy_id) <= now
        ]
        return tuple(sorted(due, key=lambda d: d.created_at)[:limit])

    def mark_audio_erased(self, workspace_id: str, meeting_id: str, when: datetime) -> None:
        self._erased.add((workspace_id, meeting_id))


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
        graph: Subgraph,
    ):
        from nodo_api.services.query import GeneratedAnswer

        if not evidence:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        citations = tuple(
            Citation(
                item.meeting_id, item.id, item.start_ms, item.end_ms, item.text, item.speaker_id
            )
            for item in evidence[:3]
        )
        entities = tuple(
            dict.fromkeys(n.label for n in graph.nodes if n.type is not EntityType.MEETING)
        )
        return GeneratedAnswer(
            self.localized_answer(locale), Answerability.SUPPORTED, citations, entities
        )


def seed_memory_adapters(
    meetings: MemoryMeetingRepository,
    transcripts: MemoryTranscriptRepository,
    retriever: MockRetriever,
    graph: Any,
) -> None:
    from nodo_api.domain.graph import (
        ExtractedEntity,
        ExtractedRelation,
        Extraction,
        MeetingRef,
        RelationKind,
        ReviewStatus,
        entity_id,
        normalize_label,
    )

    owner, workspace = "user-a", "workspace-a"
    texts = (
        ("seed-m1", "seg-001", "Marco proposed keeping the ingestion pipeline synchronous."),
        ("seed-m2", "seg-002", "Sara benchmarked an async queue for the ingestion pipeline."),
        ("seed-m3", "seg-003", "Giulia confirmed the async queue absorbs ingestion traffic peaks."),
    )
    old_decision = entity_id(
        workspace,
        owner,
        EntityType.DECISION,
        normalize_label("Keep the ingestion pipeline synchronous"),
    )
    extractions = {
        "seed-m1": (
            ("Marco", "Keep the ingestion pipeline synchronous"),
            lambda: (
                ExtractedEntity("p", EntityType.PERSON, "Marco", ("seg-001",)),
                ExtractedEntity("t", EntityType.TOPIC, "Ingestion", ("seg-001",)),
                ExtractedEntity(
                    "d",
                    EntityType.DECISION,
                    "Keep the ingestion pipeline synchronous",
                    ("seg-001",),
                ),
            ),
            (
                ExtractedRelation("d", "p", RelationKind.PROPOSED_BY, 0.9, ("seg-001",)),
                ExtractedRelation("d", "t", RelationKind.ABOUT, 0.9, ("seg-001",)),
            ),
        ),
        "seed-m2": (
            ("Sara", "Benchmark the async queue"),
            lambda: (
                ExtractedEntity("p", EntityType.PERSON, "Sara", ("seg-002",)),
                ExtractedEntity("t", EntityType.TOPIC, "Async queue", ("seg-002",)),
                ExtractedEntity("a", EntityType.ACTION, "Benchmark the async queue", ("seg-002",)),
            ),
            (
                ExtractedRelation("a", "p", RelationKind.ASSIGNED_TO, 0.9, ("seg-002",)),
                ExtractedRelation("a", "t", RelationKind.ABOUT, 0.8, ("seg-002",)),
            ),
        ),
        "seed-m3": (
            ("Giulia", "Use an async queue for ingestion"),
            lambda: (
                ExtractedEntity("p", EntityType.PERSON, "Giulia", ("seg-003",)),
                ExtractedEntity("t", EntityType.TOPIC, "Ingestion", ("seg-003",)),
                ExtractedEntity(
                    "d", EntityType.DECISION, "Use an async queue for ingestion", ("seg-003",)
                ),
            ),
            (
                ExtractedRelation("d", "p", RelationKind.PROPOSED_BY, 0.9, ("seg-003",)),
                ExtractedRelation("d", "t", RelationKind.ABOUT, 0.9, ("seg-003",)),
                ExtractedRelation(
                    "d", f"existing:{old_decision}", RelationKind.SUPERSEDES, 0.85, ("seg-003",)
                ),
            ),
        ),
    }
    for index, (meeting_id, segment_id, text) in enumerate(texts, start=1):
        created = datetime(2026, 10, index, tzinfo=UTC)
        meeting = Meeting(
            meeting_id,
            workspace,
            owner,
            created,
            MeetingState.READY,
            _seed_attestation(meeting_id),
            "NODO",
            None,
            f"Seed meeting {index}",
            text,
            2000,
        )
        meetings.add(workspace, meeting, f"seed-{index}", "seed")
        segment = TranscriptSegment(
            segment_id,
            meeting_id,
            f"S{index}",
            index * 1000,
            index * 1000 + 800,
            text,
            0.99,
            1,
            Locale.EN,
            "NODO",
        )
        transcripts.add_version(workspace, meeting_id, (segment,))
        retriever.index(workspace, owner, (segment,))
        _, build, relations = extractions[meeting_id]
        graph.upsert_extraction(
            workspace,
            owner,
            MeetingRef(meeting_id, meeting.title, created),
            Extraction(text, build(), relations),
        )
    snapshot = graph.neighborhood(
        workspace,
        owner,
        [e.id for e in graph.overview(workspace, owner, 100)],
        depth=1,
        node_limit=500,
        fanout=500,
        include_proposed=True,
        as_of=None,
    )
    for edge in snapshot.edges:
        graph.review_relation(workspace, owner, edge.id, ReviewStatus.APPROVED)


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


class MemorySpeechJobStore:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], SpeechJob] = {}

    def get(self, workspace_id: str, meeting_id: str) -> SpeechJob | None:
        return self._jobs.get((workspace_id, meeting_id))

    def start(self, workspace_id: str, meeting_id: str, handle: str, now: datetime) -> None:
        self._jobs[(workspace_id, meeting_id)] = SpeechJob(handle, now)

    def record_poll(self, workspace_id: str, meeting_id: str) -> int:
        job = self._jobs[(workspace_id, meeting_id)]
        self._jobs[(workspace_id, meeting_id)] = replace(job, polls=job.polls + 1)
        return job.polls + 1

    def clear(self, workspace_id: str, meeting_id: str) -> None:
        self._jobs.pop((workspace_id, meeting_id), None)
