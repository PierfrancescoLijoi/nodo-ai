from __future__ import annotations

import contextlib
import threading
import uuid
from dataclasses import replace

import pytest

from nodo_api.adapters.local.collections import SqliteCollectionRepository
from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.repos import SqliteMeetingRepository
from nodo_api.adapters.local.sources import SqliteSourceRepository
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from nodo_api.errors import NotFoundError
from tests.collection_contract import (
    LIMIT,
    OWNER,
    T0,
    CollectionContract,
    add_document,
    tombstone,
)
from tests.test_local_migrations import migrate_to


def _meeting(ws: str, owner: str, meeting_id: str) -> Meeting:
    attestation = RecordingAttestation(
        owner, meeting_id, "2026-10-v1", T0, "retention-30d", Locale.EN, "a", "sig"
    )
    return Meeting(meeting_id, ws, owner, T0, MeetingState.UPLOADING, attestation)


class TestSqliteCollections(CollectionContract):
    @pytest.fixture
    def db(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield db
        db.close()

    @pytest.fixture
    def sources(self, db):
        return SqliteSourceRepository(db)

    @pytest.fixture
    def meetings(self, db):
        return SqliteMeetingRepository(db)

    @pytest.fixture
    def collections(self, db):
        return SqliteCollectionRepository(db, max_collections=LIMIT)

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

    def test_tombstoning_a_source_drops_its_memberships_by_trigger(
        self, db, collections, sources, seed_meeting, tombstone_meeting, ws
    ) -> None:
        add_document(sources, ws, "d1")
        seed_meeting(ws, OWNER, "m1")
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        collections.add_source(ws, OWNER, box.id, "d1")
        collections.add_source(ws, OWNER, box.id, "m1")

        tombstone(sources, ws, "d1")
        tombstone_meeting(ws, "m1")  # through the 0009 meeting triggers

        with db.transaction(write=False) as conn:
            left = conn.execute("SELECT count(*) FROM source_collections").fetchone()[0]
        assert left == 0

    def test_add_racing_a_delete_never_leaves_a_membership(
        self, db, collections, sources, ws
    ) -> None:
        box = collections.create(ws, OWNER, name="box", parent_id=None)
        for round_ in range(20):
            sid = f"d{round_}"
            add_document(sources, ws, sid, minutes=round_)
            start = threading.Barrier(2)

            def add(sid: str = sid, start: threading.Barrier = start) -> None:
                start.wait()
                with contextlib.suppress(NotFoundError):
                    collections.add_source(ws, OWNER, box.id, sid)

            def drop(sid: str = sid, start: threading.Barrier = start) -> None:
                start.wait()
                tombstone(sources, ws, sid)

            threads = [threading.Thread(target=add), threading.Thread(target=drop)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert collections.collections_of(ws, OWNER, sid) == ()
        with db.transaction(write=False) as conn:
            assert conn.execute("SELECT count(*) FROM source_collections").fetchone()[0] == 0


def test_0014_creates_collection_tables_and_keeps_existing_data(tmp_path) -> None:
    path = tmp_path / "nodo.db"
    old = migrate_to(path, 13)
    old.execute(
        "INSERT INTO sources (workspace_id, owner_id, id, kind, title, state, created_at)"
        " VALUES ('w', 'u', 'd1', 'document', 'A title', 'ready',"
        " '2026-11-01T09:00:00.000000+00:00')"
    )
    before = [tuple(r) for r in old.execute("SELECT * FROM sources ORDER BY id")]
    old.close()

    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            triggers = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            }
            applied = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
            after = [tuple(r) for r in conn.execute("SELECT * FROM sources ORDER BY id")]
            check = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        db.close()
    assert {"collections", "source_collections"} <= tables
    assert "source_collections_forget" in triggers
    assert 14 in applied and applied == sorted(applied)
    assert after == before
    assert check == []
