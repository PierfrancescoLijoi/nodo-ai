from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.imports import SqliteImportJobRepository
from nodo_api.adapters.local.repos import SqliteMeetingRepository
from nodo_api.adapters.local.sources import SqliteSourceRepository
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from tests.import_contract import ImportContract
from tests.source_contract import OWNER, T0, source


class TestSqliteImports(ImportContract):
    @pytest.fixture
    def db(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield db
        db.close()

    @pytest.fixture
    def sources(self, db):
        return SqliteSourceRepository(db)

    @pytest.fixture
    def imports(self, db):
        return SqliteImportJobRepository(db)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed_meeting(self, db):
        meetings = SqliteMeetingRepository(db)

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

    def test_deleting_a_source_row_takes_its_job_along(self, db, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        with db.transaction() as conn:
            conn.execute("DELETE FROM sources WHERE workspace_id = ? AND id = 'd1'", (ws,))
            left = conn.execute("SELECT count(*) FROM import_jobs").fetchone()[0]
        assert left == 0
