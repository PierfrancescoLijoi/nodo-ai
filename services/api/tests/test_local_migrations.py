from __future__ import annotations

import sqlite3

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
