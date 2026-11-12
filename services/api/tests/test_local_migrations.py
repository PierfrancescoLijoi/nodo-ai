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
