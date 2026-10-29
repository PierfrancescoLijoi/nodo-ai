from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.graph import SqliteGraphStore
from tests.entity_contract import EntityVectorContract
from tests.graph_contract import GraphContract
from tests.review_contract import PendingReviewContract


class TestSqliteGraph(GraphContract, EntityVectorContract, PendingReviewContract):
    """The same behaviour tests the memory and PostgreSQL graphs pass, on the local SQLite file."""

    @pytest.fixture
    def store(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield SqliteGraphStore(db)
        db.close()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
