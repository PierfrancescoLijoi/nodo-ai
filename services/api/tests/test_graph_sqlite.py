from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.db import LocalDatabase, now_text
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.domain.graph import normalize_label
from tests.entity_contract import EntityVectorContract
from tests.graph_contract import GraphContract
from tests.review_contract import PendingReviewContract
from tests.source_graph_contract import OWNER, SourceGraphContract


class TestSqliteGraph(
    GraphContract, EntityVectorContract, PendingReviewContract, SourceGraphContract
):
    """The same behaviour tests the memory and PostgreSQL graphs pass, on the local SQLite file."""

    @pytest.fixture
    def db(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield db
        db.close()

    @pytest.fixture
    def store(self, db):
        return SqliteGraphStore(db)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed(self, db):
        def put(ws, entities, relations):
            stamp = now_text()
            with db.transaction() as conn:
                conn.executemany(
                    "INSERT INTO entities (workspace_id, owner_id, id, type, label, normalized,"
                    " ref, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ws,
                            OWNER,
                            e.id,
                            e.type.value,
                            e.label,
                            normalize_label(e.label),
                            e.source_id or e.meeting_id,
                            stamp,
                        )
                        for e in entities
                    ],
                )
                conn.executemany(
                    "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id, kind,"
                    " status, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ws,
                            OWNER,
                            r.id,
                            r.source_id,
                            r.target_id,
                            r.kind.value,
                            r.status.value,
                            r.confidence,
                            stamp,
                        )
                        for r in relations
                    ],
                )
                conn.executemany(
                    "INSERT INTO relation_evidence (workspace_id, owner_id, relation_id,"
                    " source_id, fragment_id) VALUES (?, ?, ?, ?, ?)",
                    [
                        (ws, OWNER, r.id, e.source_id, e.fragment_id)
                        for r in relations
                        for e in r.evidence
                    ],
                )

        return put


def test_a_failed_source_replace_keeps_the_previous_graph(tmp_path) -> None:
    import sqlite3

    from nodo_api.domain.graph import EntityType, Extraction
    from tests.graph_contract import ent
    from tests.source_graph_contract import HANDBOOK, doc

    db = LocalDatabase(tmp_path / "nodo.db")
    try:
        store, ws = SqliteGraphStore(db), "ws-atomic"
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        too_long = Extraction("", (ent("t", EntityType.TOPIC, "x" * 300, "f9"),), ())
        # The CHECK on the label fails after the old evidence was deleted in the transaction.
        with pytest.raises(sqlite3.IntegrityError):
            store.replace_source_evidence(ws, OWNER, doc(), too_long)
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset({"f1", "f2"})
    finally:
        db.close()
