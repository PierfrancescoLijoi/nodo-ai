from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from nodo_api.adapters.memory import (
    MemoryFragmentRepository,
    MemoryMeetingRepository,
    MemorySourceRepository,
    MemoryTranscriptRepository,
)
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from tests.source_contract import T0, FragmentContract, SourceContract


class TestMemorySources(SourceContract, FragmentContract):
    @pytest.fixture
    def meetings(self):
        return MemoryMeetingRepository()

    @pytest.fixture
    def transcripts(self):
        return MemoryTranscriptRepository()

    @pytest.fixture
    def sources(self, meetings):
        return MemorySourceRepository(meetings)

    @pytest.fixture
    def fragments(self, sources, transcripts):
        return MemoryFragmentRepository(sources, transcripts)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed_meeting(self, meetings, transcripts):
        def put(ws, owner, meeting_id, segments, minutes=0):
            created = T0 + timedelta(minutes=minutes)
            attestation = RecordingAttestation(
                owner, meeting_id, "2026-10-v1", created, "retention-30d", Locale.EN, "a", "sig"
            )
            meetings.add(
                ws,
                Meeting(meeting_id, ws, owner, created, MeetingState.UPLOADING, attestation),
                f"key-{meeting_id}",
                "hash",
            )
            if segments:
                transcripts.add_version(ws, meeting_id, segments)

        return put


def test_memory_container_merges_demo_meetings_into_sources() -> None:
    from nodo_api.bootstrap import memory_adapters
    from nodo_api.config import Settings

    adapters = memory_adapters(Settings(environment="test", auth_mode="dev", seed_demo_data=True))
    (workspace, _), meeting = next(iter(adapters.meetings._meetings.items()))
    listed = adapters.sources.list_owned(workspace, meeting.owner_id, 100, None)
    assert meeting.id in {s.id for s in listed}
    assert adapters.fragments.latest(workspace, meeting.id)
