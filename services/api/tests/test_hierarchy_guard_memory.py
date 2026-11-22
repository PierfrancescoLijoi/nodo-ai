from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import MemoryGraphStore
from tests.hierarchy_guard_contract import HierarchyGuardContract


class TestMemoryHierarchyGuard(HierarchyGuardContract):
    @pytest.fixture
    def store(self):
        return MemoryGraphStore()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
