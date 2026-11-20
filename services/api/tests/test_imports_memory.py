from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import (
    MemoryImportJobRepository,
    MemoryMeetingRepository,
    MemorySourceRepository,
)
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from tests.import_contract import ImportContract
from tests.source_contract import T0


class TestMemoryImports(ImportContract):
    @pytest.fixture
    def meetings(self):
        return MemoryMeetingRepository()

    @pytest.fixture
    def sources(self, meetings):
        return MemorySourceRepository(meetings)

    @pytest.fixture
    def imports(self, sources):
        return MemoryImportJobRepository(sources)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed_meeting(self, meetings):
        def put(ws, owner, meeting_id):
            attestation = RecordingAttestation(
                owner, meeting_id, "2026-10-v1", T0, "retention-30d", Locale.EN, "a", "sig"
            )
            meetings.add(
                ws,
                Meeting(meeting_id, ws, owner, T0, MeetingState.UPLOADING, attestation),
                f"key-{meeting_id}",
                "hash",
            )

        return put


def test_memory_container_has_an_import_repository() -> None:
    from nodo_api.bootstrap import memory_adapters
    from nodo_api.config import Settings

    adapters = memory_adapters(Settings(environment="test", auth_mode="dev"))
    assert isinstance(adapters.imports, MemoryImportJobRepository)
