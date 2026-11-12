"""SOURCE nodes behave like MEETING nodes in the graph (ADR-029) and CHILD_OF can be stored and
walked (ADR-030). Only the memory and SQLite stores run this suite: the PostgreSQL schema keeps its
narrower CHECK lists until the cloud store learns about sources.

Nothing writes source graphs yet, so each store provides a `seed(ws, entities, relations)` fixture
that stores entities and relations (with their evidence) as given; the tests then exercise the
store's own erase, sweep, review and walk."""

from __future__ import annotations

from nodo_api.domain.graph import (
    Entity,
    EntityType,
    Evidence,
    Extraction,
    Relation,
    RelationKind,
    ReviewStatus,
    relation_id,
    source_entity_id,
)
from tests.graph_contract import OWNER, eid, ent, meeting, walk

T = EntityType.TOPIC
K = RelationKind


def source_node(ws: str, source_id: str, title: str) -> Entity:
    node = source_entity_id(ws, OWNER, source_id)
    return Entity(node, EntityType.SOURCE, title, source_id=source_id)


def topic(ws: str, label: str) -> Entity:
    return Entity(eid(ws, T, label), T, label)


def edge(ws: str, source: Entity, kind: RelationKind, target: Entity, *evidence: Evidence):
    status = ReviewStatus.APPROVED if kind is K.MENTIONED_IN else ReviewStatus.PROPOSED
    rid = relation_id(ws, OWNER, source.id, kind, target.id)
    return Relation(rid, source.id, target.id, kind, status, 0.9, evidence=evidence)


def handbook(ws: str):
    """A document source mentioning two topics, one proposed as the child of the other."""
    doc = source_node(ws, "doc1", "Handbook")
    child, parent = topic(ws, "Ingestion"), topic(ws, "Platform")
    f1, f2 = Evidence("doc1", "f1"), Evidence("doc1", "f2")
    relations = [
        edge(ws, child, K.MENTIONED_IN, doc, f1),
        edge(ws, parent, K.MENTIONED_IN, doc, f2),
        edge(ws, child, K.CHILD_OF, parent, f1),
    ]
    return doc, child, parent, relations


class SourceGraphContract:
    def test_source_node_lifecycle_matches_meeting_node(self, store, seed, ws) -> None:
        doc, child, parent, relations = handbook(ws)
        seed(ws, [doc, child, parent], relations)
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("t", T, "Ingestion", "s1"),), ())
        )

        graph = walk(store, ws, [child.id], depth=1)
        nodes = {n.id: n for n in graph.nodes}
        assert nodes[doc.id].type is EntityType.SOURCE
        assert (nodes[doc.id].source_id, nodes[doc.id].meeting_id) == ("doc1", None)
        held = [n for n in graph.nodes if n.type is EntityType.MEETING]
        assert [(n.meeting_id, n.source_id) for n in held] == [("m1", None)]
        assert doc.id not in {e.id for e in store.overview(ws, OWNER, 50)}

        store.erase(ws, "doc1")  # the topic keeps its meeting mention, the rest falls
        assert {n.id for n in walk(store, ws, [child.id], depth=1).nodes} == {child.id, held[0].id}
        assert walk(store, ws, [doc.id, parent.id]).nodes == ()

        store.erase(ws, "m1")
        assert walk(store, ws, [child.id, held[0].id]).nodes == ()

    def test_source_node_lives_while_anything_targets_it(self, store, seed, ws) -> None:
        doc, child, parent, _ = handbook(ws)
        # The second mention is backed by a meeting fragment, so erasing that meeting makes the
        # source node a sweep candidate while the first mention still targets it.
        seed(
            ws,
            [doc, child, parent],
            [
                edge(ws, child, K.MENTIONED_IN, doc, Evidence("doc1", "f1")),
                edge(ws, parent, K.MENTIONED_IN, doc, Evidence("m9", "s1")),
            ],
        )
        store.erase(ws, "m9")
        assert {n.id for n in walk(store, ws, [doc.id], depth=1).nodes} == {doc.id, child.id}

        store.erase(ws, "doc1")
        assert walk(store, ws, [doc.id, child.id]).nodes == ()

    def test_erase_by_source_id_sweeps_orphans(self, store, seed, ws) -> None:
        doc, child, parent, relations = handbook(ws)
        seed(ws, [doc, child, parent], relations)
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset({"f1", "f2"})
        assert [e.id for e in store.pending_review(ws, OWNER, 10).edges] == [relations[2].id]

        store.erase(ws, "doc1")

        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset()
        assert walk(store, ws, [doc.id, child.id, parent.id]).nodes == ()
        assert store.pending_review(ws, OWNER, 10).edges == ()
        assert store.overview(ws, OWNER, 50) == ()

    def test_child_of_relation_can_be_stored_and_traversed(self, store, seed, ws) -> None:
        doc, child, parent, relations = handbook(ws)
        seed(ws, [doc, child, parent], relations)
        hierarchy = relations[2].id

        assert hierarchy in {e.id for e in walk(store, ws, [child.id], depth=1).edges}
        assert hierarchy not in {
            e.id for e in walk(store, ws, [child.id], depth=1, proposed=False).edges
        }

        approved = store.review_relation(ws, OWNER, hierarchy, ReviewStatus.APPROVED)
        assert (approved.kind, approved.status) == (K.CHILD_OF, ReviewStatus.APPROVED)
        assert approved.evidence == (Evidence("doc1", "f1"),)
        upward = walk(store, ws, [parent.id], depth=1, proposed=False)
        assert [(e.source_id, e.target_id) for e in upward.edges if e.kind is K.CHILD_OF] == [
            (child.id, parent.id)
        ]
