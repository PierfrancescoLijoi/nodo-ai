from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.repos import SqliteMeetingRepository, SqliteTranscriptRepository
from nodo_api.adapters.local.sources import SqliteFragmentRepository, SqliteSourceRepository
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from tests.source_contract import T0, FragmentContract, SourceContract


class TestSqliteSources(SourceContract, FragmentContract):
    @pytest.fixture
    def db(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield db
        db.close()

    @pytest.fixture
    def sources(self, db):
        return SqliteSourceRepository(db)

    @pytest.fixture
    def fragments(self, db):
        return SqliteFragmentRepository(db)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed_meeting(self, db):
        meetings, transcripts = SqliteMeetingRepository(db), SqliteTranscriptRepository(db)

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
