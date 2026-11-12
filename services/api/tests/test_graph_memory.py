from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import MemoryGraphStore
from nodo_api.adapters.memory.graph import _Row
from nodo_api.domain.graph import normalize_label
from tests.entity_contract import EntityVectorContract
from tests.graph_contract import GraphContract
from tests.review_contract import PendingReviewContract
from tests.source_graph_contract import OWNER, SourceGraphContract


class TestMemoryGraph(
    GraphContract, EntityVectorContract, PendingReviewContract, SourceGraphContract
):
    @pytest.fixture
    def store(self):
        return MemoryGraphStore()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def seed(self, store):
        def put(ws, entities, relations):
            part = store._part(ws, OWNER)
            for entity in entities:
                store._put_entity(part, entity, normalize_label(entity.label))
            for relation in relations:
                part.relations[relation.id] = _Row(relation, next(store._clock))

        return put
