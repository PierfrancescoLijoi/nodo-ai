from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.graph import SqliteGraphStore
from tests.hierarchy_guard_contract import HierarchyGuardContract


class TestSqliteHierarchyGuard(HierarchyGuardContract):
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
