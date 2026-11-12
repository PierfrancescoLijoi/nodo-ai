from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodo_api.adapters.memory import (
    MemoryAuditLog,
    MemoryFragmentRepository,
    MemoryGraphStore,
    MemoryMeetingRepository,
    MemorySourceRepository,
    MemoryTranscriptRepository,
)
from nodo_api.adapters.memory.answers import MemoryAnswerStore
from nodo_api.adapters.memory.graph import _Row
from nodo_api.domain.graph import normalize_label
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from nodo_api.errors import NotFoundError, ValidationFailed
from nodo_api.services.answer_cache import AnswerCache
from nodo_api.services.sources import SourceService
from tests.source_contract import OWNER, OTHER, T0, fragment, section, segment, source
from tests.source_graph_contract import handbook

WS = "workspace-a"
DELETED_AT = datetime(2026, 11, 12, 15, 30, tzinfo=UTC)


class Rig:
    def __init__(self) -> None:
        meetings = MemoryMeetingRepository()
        transcripts = MemoryTranscriptRepository()
        self.sources = MemorySourceRepository(meetings)
        self.fragments = MemoryFragmentRepository(self.sources, transcripts)
        self.graph = MemoryGraphStore()
        self.audit = MemoryAuditLog()
        self.answer_store = MemoryAnswerStore()
        answers = AnswerCache(
            self.answer_store,
            lambda _question: (1.0, 0.0),
            version="test",
            min_similarity=0.9,
        )
        self.service = SourceService(
            sources=self.sources,
            fragments=self.fragments,
            graph=self.graph,
            answers=answers,
            audit=self.audit,
            clock=lambda: DELETED_AT,
        )
        self.meetings = meetings
        self.transcripts = transcripts

    def add_document(self, source_id: str = "doc1", owner: str = OWNER) -> None:
        self.sources.add(WS, source(source_id, owner=owner, ws=WS, title="Private handbook"))
        self.fragments.add_version(
            WS,
            source_id,
            (section("s1", source_id, 0),),
            (
                fragment("f1", source_id, 0, "first", "s1"),
                fragment("f2", source_id, 1, "second", "s1"),
            ),
        )

    def seed_graph(self) -> tuple[str, str, str]:
        doc, child, parent, relations = handbook(WS)
        part = self.graph._part(WS, OWNER)
        for entity in (doc, child, parent):
            self.graph._put_entity(part, entity, normalize_label(entity.label))
        for relation in relations:
            part.relations[relation.id] = _Row(relation, next(self.graph._clock))
        return doc.id, child.id, parent.id

    def add_meeting(self, meeting_id: str = "m1") -> None:
        attestation = RecordingAttestation(
            OWNER, meeting_id, "2026-10-v1", T0, "retention-30d", Locale.EN, "a", "sig"
        )
        self.meetings.add(
            WS,
            Meeting(meeting_id, WS, OWNER, T0, MeetingState.READY, attestation),
            f"key-{meeting_id}",
            "hash",
        )
        self.transcripts.add_version(
            WS, meeting_id, (segment(meeting_id, "seg1", 0, "meeting text"),)
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_delete_removes_fragments_graph_evidence_and_bumps_cache_epoch(rig: Rig) -> None:
    rig.add_document()
    graph_ids = rig.seed_graph()
    before = rig.answer_store.epoch(WS, OWNER)

    rig.service.delete(WS, OWNER, "doc1")

    with pytest.raises(NotFoundError):
        rig.sources.get_owned(WS, OWNER, "doc1")
    assert rig.sources.get_owned(WS, OWNER, "doc1", include_tombstoned=True).tombstoned_at == (
        DELETED_AT
    )
    assert rig.fragments.latest(WS, "doc1") == ()
    assert rig.fragments.outline(WS, "doc1") == ()
    assert rig.graph.evidence_segment_ids(WS, OWNER, "doc1") == frozenset()
    assert rig.graph.neighborhood(
        WS,
        OWNER,
        graph_ids,
        depth=1,
        node_limit=10,
        fanout=10,
        include_proposed=True,
        as_of=None,
    ).nodes == ()
    assert rig.answer_store.epoch(WS, OWNER) == before + 1


def test_delete_other_users_source_is_not_found(rig: Rig) -> None:
    rig.add_document()

    with pytest.raises(NotFoundError):
        rig.service.delete(WS, OTHER, "doc1")

    assert len(rig.fragments.latest(WS, "doc1")) == 2
    assert rig.sources.get_owned(WS, OWNER, "doc1").tombstoned_at is None
    assert rig.audit._events == {}


def test_delete_meeting_source_is_refused(rig: Rig) -> None:
    rig.add_meeting()

    with pytest.raises(ValidationFailed):
        rig.service.delete(WS, OWNER, "m1")

    assert rig.sources.get_owned(WS, OWNER, "m1").kind.value == "meeting"
    assert [item.id for item in rig.fragments.latest(WS, "m1")] == ["seg1"]
    assert rig.audit._events == {}


def test_delete_records_audit_without_title(rig: Rig) -> None:
    rig.add_document()

    rig.service.delete(WS, OWNER, "doc1")

    assert len(rig.audit._events) == 1
    event_type, metadata = next(iter(rig.audit._events.values()))
    assert event_type == "source_deleted"
    assert metadata == {"kind": "document"}
    assert "Private handbook" not in repr(rig.audit._events)
