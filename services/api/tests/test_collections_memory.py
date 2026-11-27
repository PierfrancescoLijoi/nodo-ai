from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from nodo_api.adapters.memory import (
    MemoryCollectionRepository,
    MemoryMeetingRepository,
    MemorySourceRepository,
)
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from tests.collection_contract import LIMIT, OWNER, T0, CollectionContract, add_document


def _meeting(ws: str, owner: str, meeting_id: str) -> Meeting:
    attestation = RecordingAttestation(
        owner, meeting_id, "2026-10-v1", T0, "retention-30d", Locale.EN, "a", "sig"
    )
    return Meeting(meeting_id, ws, owner, T0, MeetingState.UPLOADING, attestation)


class TestMemoryCollections(CollectionContract):
    @pytest.fixture
    def meetings(self):
        return MemoryMeetingRepository()

    @pytest.fixture
    def sources(self, meetings):
        return MemorySourceRepository(meetings)

    @pytest.fixture
    def collections(self, sources):
        return MemoryCollectionRepository(sources, max_collections=LIMIT)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed_meeting(self, meetings):
        def put(ws, owner, meeting_id):
            meetings.add(ws, _meeting(ws, owner, meeting_id), f"key-{meeting_id}", "hash")

        return put

    @pytest.fixture
    def tombstone_meeting(self, meetings):
        def drop(ws, meeting_id):
            meeting = meetings.get_for_processing(ws, meeting_id)
            meetings.save(ws, replace(meeting, tombstoned_at=T0))

        return drop


def test_source_delete_and_meeting_delete_forget_memberships() -> None:
    from nodo_api.config import Settings
    from nodo_api.main import _container

    container = _container(Settings(environment="test", auth_mode="dev"), None)
    collections = container.collections
    assert isinstance(collections, MemoryCollectionRepository)
    ws = "ws-delete"
    add_document(container.sources, ws, "d1")
    container.meetings.add(ws, _meeting(ws, OWNER, "m1"), "key-m1", "hash")
    box = collections.create(ws, OWNER, name="box", parent_id=None)
    collections.add_source(ws, OWNER, box.id, "d1")
    collections.add_source(ws, OWNER, box.id, "m1")

    container.source_service.delete(ws, OWNER, "d1")
    container.meeting_service.delete(ws, OWNER, "m1")

    # The memory store has no trigger: only the explicit erase calls remove these.
    assert all(not members for members in collections._members.values())
    assert collections.get_owned(ws, OWNER, box.id).source_count == 0
