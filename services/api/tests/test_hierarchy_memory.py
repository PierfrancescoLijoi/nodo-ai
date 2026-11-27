from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import MemoryGraphStore
from nodo_api.adapters.memory.graph import _Row
from nodo_api.domain.graph import Evidence, Relation, RelationKind, ReviewStatus, relation_id
from tests.graph_contract import OWNER
from tests.hierarchy_contract import HierarchyContract


class TestMemoryHierarchy(HierarchyContract):
    @pytest.fixture
    def store(self):
        return MemoryGraphStore()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def raw_link(self, store):
        def add(ws: str, child: str, parent: str, status=ReviewStatus.APPROVED, evidence=True):
            rid = relation_id(ws, OWNER, child, RelationKind.CHILD_OF, parent)
            relation = Relation(
                rid,
                child,
                parent,
                RelationKind.CHILD_OF,
                status,
                0.9,
                None,
                None,
                (Evidence("doc1", "f1"),) if evidence else (),
            )
            store._parts[(ws, OWNER)].relations[rid] = _Row(relation, 10_000)

        return add
