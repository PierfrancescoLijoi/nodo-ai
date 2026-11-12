from __future__ import annotations

import sqlite3

import pytest

from nodo_api.adapters.local import db as db_module
from nodo_api.adapters.local.db import LocalDatabase, migration_files, now_text
from nodo_api.adapters.local.graph import SqliteGraphStore


def migrate_to(path, version):
    """A database as an older release left it: migrations applied up to and including `version`."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    for number, name, script in migration_files():
        if number > version:
            break
        conn.executescript(
            f"BEGIN IMMEDIATE;\n{script}\n"
            f"INSERT INTO schema_migrations VALUES ({number}, '{name}', '{now_text()}');\nCOMMIT;"
        )
    return conn


def test_evidence_columns_renamed_and_data_kept(tmp_path):
    path = tmp_path / "nodo.db"
    old = migrate_to(path, 6)
    columns = {r[1] for r in old.execute("PRAGMA table_info(relation_evidence)")}
    assert {"meeting_id", "segment_id"} <= columns
    stamp = now_text()
    old.execute(
        "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind, status,"
        " confidence, created_at) VALUES ('w', 'u', 'r1', 'e1', 'e2', 'ABOUT', 'approved', 0.9, ?)",
        (stamp,),
    )
    old.execute("INSERT INTO relation_evidence VALUES ('w', 'u', 'r1', 'm1', 's1')")
    old.close()

    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(relation_evidence)")}
            row = conn.execute("SELECT source_id, fragment_id FROM relation_evidence").fetchone()
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT 1 FROM relation_evidence"
                " WHERE workspace_id = 'w' AND source_id = 'm1'"
            ).fetchall()
        assert "meeting_id" not in columns and "segment_id" not in columns
        assert tuple(row) == ("m1", "s1")
        assert any("evidence_meeting_idx" in r["detail"] for r in plan)
        assert SqliteGraphStore(db).evidence_segment_ids("w", "u", "m1") == frozenset({"s1"})
    finally:
        db.close()


def _graph_at_0007(path):
    old = migrate_to(path, 7)
    stamp = now_text()
    old.executemany(
        "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized, ref,"
        " updated_at) VALUES ('w', 'u', ?, ?, ?, ?, ?, ?)",
        [
            ("e-meeting", "meeting", "Kickoff", "m1", "m1", stamp),
            ("e-topic", "topic", "Ingestion", "ingestion", None, stamp),
        ],
    )
    old.executemany(
        "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind, status,"
        " confidence, created_at) VALUES ('w', 'u', ?, 'e-topic', 'e-meeting', ?, ?, ?, ?)",
        [
            ("r1", "MENTIONED_IN", "approved", 1.0, stamp),
            ("r2", "DECIDED_IN", "proposed", 0.5, stamp),
        ],
    )
    old.executemany(
        "INSERT INTO relation_evidence VALUES ('w', 'u', ?, 'm1', ?)",
        [("r1", "s1"), ("r1", "s2"), ("r2", "s1")],
    )
    old.execute("INSERT INTO entity_vectors VALUES ('w', 'u', 'e-topic', 2, x'0000803f00000000')")
    old.close()


def test_rebuild_keeps_relations_and_evidence(tmp_path):
    path = tmp_path / "nodo.db"
    _graph_at_0007(path)

    db = LocalDatabase(path)
    try:
        with db.transaction() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            entities = conn.execute("SELECT id, type, ref FROM entities ORDER BY id").fetchall()
            relations = conn.execute(
                "SELECT id, kind, status FROM relations ORDER BY id"
            ).fetchall()
            evidence = conn.execute(
                "SELECT relation_id, source_id, fragment_id FROM relation_evidence ORDER BY 1, 3"
            ).fetchall()
            assert [tuple(r) for r in entities] == [
                ("e-meeting", "meeting", "m1"),
                ("e-topic", "topic", None),
            ]
            assert [tuple(r) for r in relations] == [
                ("r1", "MENTIONED_IN", "approved"),
                ("r2", "DECIDED_IN", "proposed"),
            ]
            assert [tuple(r) for r in evidence] == [
                ("r1", "m1", "s1"),
                ("r1", "m1", "s2"),
                ("r2", "m1", "s1"),
            ]
            assert conn.execute("SELECT count(*) FROM entity_vectors").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

            # The cascades still point at the rebuilt tables.
            conn.execute("DELETE FROM relations WHERE id = 'r1'")
            left = conn.execute("SELECT relation_id FROM relation_evidence").fetchall()
            assert [r[0] for r in left] == ["r2"]
            conn.execute("DELETE FROM entities WHERE id = 'e-topic'")
            assert conn.execute("SELECT count(*) FROM entity_vectors").fetchone()[0] == 0

            # The widened CHECK lists.
            conn.execute(
                "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized, ref,"
                " updated_at) VALUES ('w', 'u', 'e-doc', 'source', 'Handbook', 'doc1', 'doc1', ?)",
                (now_text(),),
            )
            conn.execute(
                "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                " status, confidence, created_at)"
                " VALUES ('w', 'u', 'r3', 'a', 'b', 'CHILD_OF', 'proposed', 0.7, ?)",
                (now_text(),),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized,"
                    " updated_at) VALUES ('w', 'u', 'e-x', 'fragment', 'x', 'x', ?)",
                    (now_text(),),
                )
    finally:
        db.close()


def test_rebuild_leaves_no_dangling_table_names(tmp_path):
    path = tmp_path / "nodo.db"
    _graph_at_0007(path)
    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            schema = {
                r["name"]: r["sql"] or ""
                for r in conn.execute("SELECT name, sql FROM sqlite_master")
            }
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM entities WHERE workspace_id = 'w'"
                " AND owner_id = 'u' AND type NOT IN ('meeting', 'source')"
                " ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
    finally:
        db.close()
    assert not [n for n, sql in schema.items() if "_new" in n + sql or "_old" in n + sql]
    assert "REFERENCES relations " in schema["relation_evidence"]
    assert "REFERENCES entities " in schema["entity_vectors"]
    assert "'source'" in schema["entities"] and "'CHILD_OF'" in schema["relations"]
    assert "'source'" in schema["entities_recent_idx"]
    for index in ("relations_out_idx", "relations_in_idx", "relations_changes_idx"):
        assert index in schema
    assert any("entities_recent_idx" in r["detail"] for r in plan)


def _with_extra_migration(monkeypatch, script):
    real = migration_files()
    monkeypatch.setattr(
        db_module, "migration_files", lambda: [*real, (99, "0099_test.sql", script)]
    )


def test_foreign_key_check_runs_after_rebuild(tmp_path, monkeypatch):
    path = tmp_path / "nodo.db"
    LocalDatabase(path).close()
    _with_extra_migration(
        monkeypatch,
        "-- nodo:foreign-keys-off\n"
        "INSERT INTO relation_evidence VALUES ('w', 'u', 'missing', 'm1', 's1');\n"
        "CREATE TABLE should_vanish (x INTEGER);\n",
    )
    with pytest.raises(sqlite3.IntegrityError, match="foreign key"):
        LocalDatabase(path)

    conn = sqlite3.connect(path)
    try:
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        orphans = conn.execute("SELECT count(*) FROM relation_evidence").fetchone()[0]
    finally:
        conn.close()
    assert 99 not in versions and "should_vanish" not in tables and orphans == 0


def test_foreign_keys_are_off_during_and_on_after_the_migration(tmp_path, monkeypatch):
    path = tmp_path / "nodo.db"
    LocalDatabase(path).close()
    # With foreign keys on, the insert would fail at once; off, it is repaired before the check.
    _with_extra_migration(
        monkeypatch,
        "-- nodo:foreign-keys-off\n"
        "INSERT INTO relation_evidence VALUES ('w', 'u', 'later', 'm1', 's1');\n"
        "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind, status,"
        " confidence, created_at) VALUES ('w', 'u', 'later', 'a', 'b', 'ABOUT', 'approved', 1,"
        " '2026-11-12');\n",
    )
    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 99
    finally:
        db.close()


def test_foreign_keys_are_back_on_after_a_failed_migration(tmp_path, monkeypatch):
    path = tmp_path / "nodo.db"
    db = LocalDatabase(path)
    _with_extra_migration(monkeypatch, "-- nodo:foreign-keys-off\nTHIS IS NOT SQL;\n")
    try:
        with pytest.raises(sqlite3.OperationalError):
            db._migrate()
        conn = db.connection()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not conn.in_transaction
    finally:
        db.close()


# --- 0009: sources, sections and fragments ------------------------------------------------------


def _meeting_row(conn, meeting_id, *, state="ready", tombstoned_at=None, title=None, ws="w"):
    conn.execute(
        "INSERT INTO meetings (workspace_id, id, owner_id, state, project, created_at,"
        " tombstoned_at, title) VALUES (?, ?, 'u', ?, 'NODO', ?, ?, ?)",
        (
            ws,
            meeting_id,
            state,
            f"2026-10-0{meeting_id[-1]}T00:00:00.000000+00:00",
            tombstoned_at,
            title,
        ),
    )


def _source(conn, meeting_id, ws="w"):
    return conn.execute(
        "SELECT * FROM sources WHERE workspace_id = ? AND id = ?", (ws, meeting_id)
    ).fetchone()


def test_existing_meetings_are_backfilled_as_sources(tmp_path):
    path = tmp_path / "nodo.db"
    old = migrate_to(path, 8)
    _meeting_row(old, "m1", title="Kickoff")
    _meeting_row(old, "m2", state="failed", tombstoned_at="2026-10-05T00:00:00.000000+00:00")
    old.execute(
        "INSERT INTO transcript_segments VALUES ('w', 'm1', 1, 's1', 'S1', 0, 10, 'hello', 0.9,"
        " 'en', 'NODO')"
    )
    before = [tuple(r) for r in old.execute("SELECT * FROM meetings ORDER BY id")]
    old.close()

    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            sources = {r["id"]: r for r in conn.execute("SELECT * FROM sources")}
            after = [tuple(r) for r in conn.execute("SELECT * FROM meetings ORDER BY id")]
            segments = conn.execute("SELECT count(*) FROM transcript_segments").fetchone()[0]
    finally:
        db.close()
    assert after == before and segments == 1
    assert set(sources) == {"m1", "m2"}
    one, two = sources["m1"], sources["m2"]
    assert (one["workspace_id"], one["owner_id"], one["kind"], one["version"]) == (
        "w",
        "u",
        "meeting",
        1,
    )
    assert (one["title"], one["state"], one["tombstoned_at"]) == ("Kickoff", "ready", None)
    assert one["created_at"] == "2026-10-01T00:00:00.000000+00:00"
    assert (two["state"], two["tombstoned_at"]) == ("failed", "2026-10-05T00:00:00.000000+00:00")
    assert all(r["origin"] is None and r["content_hash"] is None for r in sources.values())


def test_new_meeting_appears_as_source_with_same_id_and_state(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _meeting_row(conn, "m3", state="uploading")
            row = _source(conn, "m3")
        assert (row["kind"], row["state"], row["owner_id"], row["version"]) == (
            "meeting",
            "uploading",
            "u",
            1,
        )
    finally:
        db.close()


def test_meeting_update_and_tombstone_reach_sources(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _meeting_row(conn, "m1", state="uploading")
            conn.execute("UPDATE sources SET origin = 'mic', content_hash = 'h' WHERE id = 'm1'")
            conn.execute(
                "UPDATE meetings SET state = 'ready', title = 'Renamed', project = 'Other',"
                " summary = 'x' WHERE id = 'm1'"
            )
            row = _source(conn, "m1")
            assert (row["state"], row["title"]) == ("ready", "Renamed")
            assert (row["origin"], row["content_hash"], row["owner_id"]) == ("mic", "h", "u")
            assert row["tombstoned_at"] is None
            conn.execute(
                "UPDATE meetings SET tombstoned_at = '2026-10-09T00:00:00.000000+00:00'"
                " WHERE id = 'm1'"
            )
            assert _source(conn, "m1")["tombstoned_at"] == "2026-10-09T00:00:00.000000+00:00"
            columns = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
            assert "project" not in columns
    finally:
        db.close()


def test_meeting_repository_writes_reach_sources(tmp_path):
    from dataclasses import replace
    from datetime import UTC, datetime

    from nodo_api.adapters.local.db import to_text
    from nodo_api.adapters.local.repos import SqliteMeetingRepository
    from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation

    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        created = datetime(2026, 10, 1, tzinfo=UTC)
        meeting = Meeting(
            id="m1",
            workspace_id="w",
            owner_id="u",
            created_at=created,
            state=MeetingState.UPLOADING,
            attestation=RecordingAttestation(
                "u", "m1", "v1", created, "retention-30d", Locale.EN, "audit-1", "sig"
            ),
            project="NODO",
        )
        repo = SqliteMeetingRepository(db)
        repo.add("w", meeting, "key-1", "hash-1")
        repo.add("w", meeting, "key-1", "hash-1")  # idempotent replay writes nothing new
        deleted = datetime(2026, 10, 2, tzinfo=UTC)
        ready = replace(meeting, state=MeetingState.READY, title="T")
        repo.save("w", ready)
        with db.transaction(write=False) as conn:
            row = conn.execute("SELECT * FROM sources").fetchone()
        assert (row["state"], row["title"], row["tombstoned_at"]) == ("ready", "T", None)
        repo.save("w", replace(ready, tombstoned_at=deleted, project="Other"))
        with db.transaction(write=False) as conn:
            rows = conn.execute("SELECT * FROM sources").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == "m1" and rows[0]["kind"] == "meeting"
        assert rows[0]["created_at"] == to_text(created)
        assert rows[0]["tombstoned_at"] == to_text(deleted)
    finally:
        db.close()


def test_insert_or_replace_of_a_meeting_does_not_duplicate_or_fail(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _meeting_row(conn, "m1", state="uploading")
            conn.execute(
                "INSERT OR REPLACE INTO meetings (workspace_id, id, owner_id, state, created_at,"
                " title) VALUES ('w', 'm1', 'u', 'ready', '2026-10-01T00:00:00.000000+00:00', 'T')"
            )
            rows = conn.execute("SELECT state, title FROM sources").fetchall()
        assert [tuple(r) for r in rows] == [("ready", "T")]
    finally:
        db.close()


def test_meeting_delete_removes_source(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _meeting_row(conn, "m1")
            _meeting_row(conn, "m2")
            conn.execute("DELETE FROM meetings WHERE id = 'm1'")
            assert _source(conn, "m1") is None
            assert _source(conn, "m2") is not None
    finally:
        db.close()


def _insert_document(conn, source_id="d1", kind="document"):
    conn.execute(
        "INSERT INTO sources (workspace_id, owner_id, id, kind, title, state, created_at)"
        " VALUES ('w', 'u', ?, ?, 'Handbook', 'ready', '2026-10-01T00:00:00.000000+00:00')",
        (source_id, kind),
    )


def _insert_fragment(conn, fragment_id, text, source_id="d1"):
    conn.execute(
        "INSERT INTO fragments (workspace_id, source_id, version, id, section_id, ordinal, text,"
        " locator) VALUES ('w', ?, 1, ?, 'sec1', 0, ?, '{\"page\":1}')",
        (source_id, fragment_id, text),
    )


def test_deleting_a_source_cascades_to_sections_and_fragments(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _insert_document(conn, "d1")
            _insert_document(conn, "d2")
            for source_id in ("d1", "d2"):
                conn.execute(
                    "INSERT INTO sections (workspace_id, source_id, id, parent_id, ordinal, level,"
                    " title) VALUES ('w', ?, 'sec1', NULL, 0, 1, 'Intro')",
                    (source_id,),
                )
                _insert_fragment(conn, "f1", "Some text", source_id)
            conn.execute("DELETE FROM sources WHERE id = 'd1'")
            sections = conn.execute("SELECT source_id FROM sections").fetchall()
            fragments = conn.execute("SELECT source_id FROM fragments").fetchall()
            assert [r[0] for r in sections] == ["d2"] and [r[0] for r in fragments] == ["d2"]
            with pytest.raises(sqlite3.IntegrityError):  # a fragment needs its source
                _insert_fragment(conn, "f9", "orphan", "missing")
    finally:
        db.close()


def test_fragment_text_limit_enforced(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            _insert_document(conn)
            _insert_fragment(conn, "ok", "x" * 20_000)
            for bad in ("", "x" * 20_001):
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute("SAVEPOINT s")
                    try:
                        _insert_fragment(conn, "bad", bad)
                    finally:
                        conn.execute("ROLLBACK TO s")
                        conn.execute("RELEASE s")
    finally:
        db.close()


def test_source_kind_is_restricted_and_listing_uses_its_index(tmp_path):
    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        with db.transaction() as conn:
            for kind in ("document", "deck", "web", "note", "audio"):
                _insert_document(conn, f"s-{kind}", kind)
            with pytest.raises(sqlite3.IntegrityError):
                _insert_document(conn, "s-bad", "spreadsheet")
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM sources WHERE workspace_id = 'w'"
                " AND owner_id = 'u' AND tombstoned_at IS NULL ORDER BY created_at DESC, id DESC"
            ).fetchall()
        assert any("sources_list_idx" in r["detail"] for r in plan)
    finally:
        db.close()
