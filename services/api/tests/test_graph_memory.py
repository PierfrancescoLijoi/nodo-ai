from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import MemoryGraphStore
from tests.entity_contract import EntityVectorContract
from tests.graph_contract import GraphContract
from tests.review_contract import PendingReviewContract


class TestMemoryGraph(GraphContract, EntityVectorContract, PendingReviewContract):
    @pytest.fixture
    def store(self):
        return MemoryGraphStore()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
