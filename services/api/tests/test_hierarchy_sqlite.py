from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.db import LocalDatabase, now_text
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.domain.graph import RelationKind, ReviewStatus, relation_id
from tests.graph_contract import OWNER
from tests.hierarchy_contract import HierarchyContract


class TestSqliteHierarchy(HierarchyContract):
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
    def raw_link(self, db):
        def add(ws: str, child: str, parent: str, status=ReviewStatus.APPROVED, evidence=True):
            rid = relation_id(ws, OWNER, child, RelationKind.CHILD_OF, parent)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO relations (workspace_id, owner_id, id, source_id, target_id,"
                    " kind, status, confidence, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 'CHILD_OF', ?, 0.9, ?)",
                    (ws, OWNER, rid, child, parent, status.value, now_text()),
                )
                if evidence:
                    conn.execute(
                        "INSERT INTO relation_evidence (workspace_id, owner_id, relation_id,"
                        " source_id, fragment_id) VALUES (?, ?, ?, 'doc1', 'f1')",
                        (ws, OWNER, rid),
                    )

        return add
