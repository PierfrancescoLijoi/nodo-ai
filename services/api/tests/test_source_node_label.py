"""The SOURCE graph node label follows the current source title on every evidence write (Phase 3,
Task 2). Concepts keep their first-writer label and meeting nodes are left alone."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.memory.graph import MemoryGraphStore
from nodo_api.domain.graph import (
    EntityType,
    Extraction,
    SourceRef,
    meeting_entity_id,
    source_entity_id,
)
from nodo_api.domain.sources import SourceKind
from tests.graph_contract import OWNER, eid, ent, meeting, walk

T = EntityType.TOPIC
TOPICS = Extraction("", (ent("t", T, "Ingestion", "f1"),), ())


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield MemoryGraphStore()
        return
    db = LocalDatabase(tmp_path / "nodo.db")
    yield SqliteGraphStore(db)
    db.close()


@pytest.fixture
def ws() -> str:
    return f"ws-{uuid.uuid4().hex[:8]}"


def doc(title: str | None, source_id: str = "doc1", kind=SourceKind.DOCUMENT) -> SourceRef:
    return SourceRef(source_id, title, datetime(2026, 11, 1, tzinfo=UTC), kind)


def label_of(store, ws: str, node_id: str) -> str:
    return next(n.label for n in walk(store, ws, [node_id], depth=1).nodes if n.id == node_id)


def test_source_node_label_follows_the_current_title(store, ws) -> None:
    node = source_entity_id(ws, OWNER, "doc1")
    store.replace_source_evidence(ws, OWNER, doc("Handbook"), TOPICS)
    assert label_of(store, ws, node) == "Handbook"

    store.replace_source_evidence(ws, OWNER, doc("  Field   guide "), TOPICS)
    assert label_of(store, ws, node) == "Field guide"


def test_title_removed_falls_back_to_the_kind(store, ws) -> None:
    node = source_entity_id(ws, OWNER, "doc1")
    store.replace_source_evidence(ws, OWNER, doc("Handbook"), TOPICS)
    store.replace_source_evidence(ws, OWNER, doc(None), TOPICS)
    assert label_of(store, ws, node) == SourceKind.DOCUMENT.value


def test_relabelling_the_source_never_relabels_a_concept(store, ws) -> None:
    topic = eid(ws, T, "Ingestion")
    store.replace_source_evidence(ws, OWNER, doc("Handbook"), TOPICS)
    other = Extraction("", (ent("t", T, "INGESTION", "f9"),), ())
    store.replace_source_evidence(ws, OWNER, doc("Notes", "doc2"), other)
    store.replace_source_evidence(ws, OWNER, doc("Renamed"), other)

    assert label_of(store, ws, topic) == "Ingestion"
    assert label_of(store, ws, source_entity_id(ws, OWNER, "doc1")) == "Renamed"
    assert label_of(store, ws, source_entity_id(ws, OWNER, "doc2")) == "Notes"


def test_meeting_nodes_are_not_touched(store, ws) -> None:
    node = meeting_entity_id(ws, OWNER, "m1")
    shared = Extraction("", (ent("t", T, "Ingestion", "s1"),), ())
    store.upsert_extraction(ws, OWNER, meeting(1, "Standup"), shared)
    store.replace_source_evidence(ws, OWNER, doc("Handbook"), TOPICS)
    store.replace_source_evidence(ws, OWNER, doc("Renamed"), TOPICS)
    assert label_of(store, ws, node) == "Standup"


def test_no_source_title_old_or_new_is_ever_searchable(store, ws) -> None:
    """The source node is keyed for search by its id, not its title, and search skips source
    nodes, so a rename leaves no old title behind and adds no new one."""
    store.replace_source_evidence(ws, OWNER, doc("Handbook"), TOPICS)
    store.replace_source_evidence(ws, OWNER, doc("Renamed"), TOPICS)
    assert store.find_entities(ws, OWNER, ["handbook"], 10) == ()
    assert store.find_entities(ws, OWNER, ["renamed"], 10) == ()
    assert [e.label for e in store.find_entities(ws, OWNER, ["ingestion"], 10)] == ["Ingestion"]
