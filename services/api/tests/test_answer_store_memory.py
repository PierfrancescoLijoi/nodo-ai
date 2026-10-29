from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.local.answers import SqliteAnswerStore
from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.memory.answers import MemoryAnswerStore
from tests.answer_cache_contract import AnswerStoreContract


class TestMemoryAnswerStore(AnswerStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryAnswerStore()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"


class TestSqliteAnswerStore(AnswerStoreContract):
    @pytest.fixture
    def store(self, tmp_path):
        db = LocalDatabase(tmp_path / "nodo.db")
        yield SqliteAnswerStore(db)
        db.close()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
