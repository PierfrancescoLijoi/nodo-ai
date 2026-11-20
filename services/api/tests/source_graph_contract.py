"""SOURCE nodes behave like MEETING nodes in the graph (ADR-029) and CHILD_OF can be stored and
walked (ADR-030). Only the memory and SQLite stores run this suite: the PostgreSQL schema keeps its
narrower CHECK lists until the cloud store learns about sources.

The first tests use a `seed(ws, entities, relations)` fixture that stores entities and relations
as given; the later ones write through `replace_source_evidence`, the path imports use."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from nodo_api.domain.graph import (
    Entity,
    EntityType,
    Evidence,
    Extraction,
    Relation,
    RelationKind,
    ReviewStatus,
    SourceRef,
    relation_id,
    source_entity_id,
)
from nodo_api.domain.sources import SourceKind
from nodo_api.errors import NotFoundError
from nodo_api.services.graph import GraphService
from nodo_api.services.query import QueryService
from tests.graph_contract import OWNER, eid, ent, meeting, rel, walk

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


def doc(source_id: str = "doc1", title: str | None = "Handbook") -> SourceRef:
    return SourceRef(source_id, title, datetime(2026, 11, 1, tzinfo=UTC), SourceKind.DOCUMENT)


# Ingestion on f1, Platform on f2, Ingestion proposed as the child of Platform on f1.
HANDBOOK = Extraction(
    "",
    (ent("c", T, "Ingestion", "f1"), ent("p", T, "Platform", "f2")),
    (rel("c", K.CHILD_OF, "p", "f1"),),
)


def mentions(store, ws: str, entity_id: str, owner: str = OWNER) -> set[Evidence]:
    graph = walk(store, ws, [entity_id], depth=1, owner=owner)
    return {
        e
        for edge in graph.edges
        if edge.kind is K.MENTIONED_IN and edge.source_id == entity_id
        for e in edge.evidence
    }


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

    # --- written by replace_source_evidence ----------------------------------------------------

    def test_source_node_written_by_replace_source_evidence_follows_the_lifecycle(
        self, store, ws
    ) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("t", T, "Ingestion", "s1"),), ())
        )
        node = source_entity_id(ws, OWNER, "doc1")
        child, parent = eid(ws, T, "Ingestion"), eid(ws, T, "Platform")

        graph = walk(store, ws, [child], depth=1)
        nodes = {n.id: n for n in graph.nodes}
        assert (nodes[node].type, nodes[node].label) == (EntityType.SOURCE, "Handbook")
        assert (nodes[node].source_id, nodes[node].meeting_id) == ("doc1", None)
        held = [n for n in graph.nodes if n.type is EntityType.MEETING]
        assert [n.meeting_id for n in held] == ["m1"]
        kinds = {(e.kind, e.target_id): e for e in graph.edges if e.source_id == child}
        assert kinds[(K.MENTIONED_IN, node)].status is ReviewStatus.APPROVED
        assert kinds[(K.MENTIONED_IN, node)].evidence == (Evidence("doc1", "f1"),)
        assert kinds[(K.CHILD_OF, parent)].status is ReviewStatus.PROPOSED
        assert node not in {e.id for e in store.overview(ws, OWNER, 50)}
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset({"f1", "f2"})

        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)  # idempotent
        assert {n.id for n in walk(store, ws, [child], depth=1).nodes} == set(nodes)

        store.erase(ws, "doc1")  # the topic keeps its meeting mention, the rest falls
        assert {n.id for n in walk(store, ws, [child], depth=1).nodes} == {child, held[0].id}
        assert walk(store, ws, [node, parent]).nodes == ()

    def test_a_source_without_title_or_mentions_leaves_no_stray_node(self, store, ws) -> None:
        store.replace_source_evidence(ws, OWNER, doc("doc2", None), HANDBOOK)
        node = source_entity_id(ws, OWNER, "doc2")
        assert {n.label for n in walk(store, ws, [node]).nodes if n.id == node} == {"document"}

        store.replace_source_evidence(ws, OWNER, doc("doc2", None), Extraction())
        assert walk(store, ws, [node]).nodes == ()
        store.replace_source_evidence(ws, OWNER, doc("doc3"), Extraction())
        assert walk(store, ws, [source_entity_id(ws, OWNER, "doc3")]).nodes == ()

    def test_replace_drops_only_listed_fragment_evidence(self, store, ws) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        # The same fragment id in another source and a meeting mention of the same topics.
        store.replace_source_evidence(
            ws, OWNER, doc("doc2"), Extraction("", (ent("p", T, "Platform", "f2"),), ())
        )
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("t", T, "Ingestion", "s1"),), ())
        )
        child, parent = eid(ws, T, "Ingestion"), eid(ws, T, "Platform")

        store.replace_source_evidence(
            ws, OWNER, doc(), Extraction(), dropped_fragment_ids=frozenset({"f2"})
        )
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset({"f1"})
        assert store.evidence_segment_ids(ws, OWNER, "doc2") == frozenset({"f2"})
        assert store.evidence_segment_ids(ws, OWNER, "m1") == frozenset({"s1"})
        assert mentions(store, ws, parent) == {Evidence("doc2", "f2")}

        store.replace_source_evidence(
            ws, OWNER, doc(), Extraction(), dropped_fragment_ids=frozenset()
        )
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset({"f1"})

        store.replace_source_evidence(ws, OWNER, doc(), Extraction())
        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset()
        assert walk(store, ws, [source_entity_id(ws, OWNER, "doc1")]).nodes == ()
        assert mentions(store, ws, child) == {Evidence("m1", "s1")}
        assert mentions(store, ws, parent) == {Evidence("doc2", "f2")}

    def test_unchanged_fragments_keep_their_approved_relation(self, store, ws) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        child, parent = eid(ws, T, "Ingestion"), eid(ws, T, "Platform")
        hierarchy = relation_id(ws, OWNER, child, K.CHILD_OF, parent)
        store.review_relation(ws, OWNER, hierarchy, ReviewStatus.APPROVED)

        # f2 changed into f3: the relation backed by f1 keeps its review.
        store.replace_source_evidence(
            ws,
            OWNER,
            doc(),
            Extraction("", (ent("p", T, "Platform", "f3"),), ()),
            dropped_fragment_ids=frozenset({"f2"}),
        )
        edges = {e.id: e for e in walk(store, ws, [child], depth=1).edges}
        assert edges[hierarchy].status is ReviewStatus.APPROVED
        assert edges[hierarchy].evidence == (Evidence("doc1", "f1"),)
        assert mentions(store, ws, parent) == {Evidence("doc1", "f3")}
        assert (child, parent) == (eid(ws, T, "Ingestion"), eid(ws, T, "Platform"))

        # f1 changed too and the new text no longer supports the hierarchy: it is swept.
        store.replace_source_evidence(
            ws,
            OWNER,
            doc(),
            Extraction("", (ent("c", T, "Ingestion", "f4"),), ()),
            dropped_fragment_ids=frozenset({"f1"}),
        )
        assert hierarchy not in {e.id for e in walk(store, ws, [child], depth=1).edges}
        assert mentions(store, ws, child) == {Evidence("doc1", "f4")}

    def test_erase_by_source_id_sweeps_a_written_source_graph(self, store, ws) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        child, parent = eid(ws, T, "Ingestion"), eid(ws, T, "Platform")
        hierarchy = relation_id(ws, OWNER, child, K.CHILD_OF, parent)
        assert [e.id for e in store.pending_review(ws, OWNER, 10).edges] == [hierarchy]

        store.erase(ws, "doc1")

        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset()
        node = source_entity_id(ws, OWNER, "doc1")
        assert walk(store, ws, [node, child, parent]).nodes == ()
        assert store.pending_review(ws, OWNER, 10).edges == ()
        assert store.overview(ws, OWNER, 50) == ()

    def test_replace_never_touches_another_owner(self, store, ws) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        store.replace_source_evidence(ws, "bob", doc(), HANDBOOK)

        store.replace_source_evidence(ws, OWNER, doc(), Extraction())

        assert store.evidence_segment_ids(ws, OWNER, "doc1") == frozenset()
        assert store.evidence_segment_ids(ws, "bob", "doc1") == frozenset({"f1", "f2"})
        bob_child = eid(ws, T, "Ingestion", owner="bob")
        assert mentions(store, ws, bob_child, owner="bob") == {Evidence("doc1", "f1")}

    def test_a_document_decision_is_decided_in_its_source(self, store, ws) -> None:
        decision = ent("d", EntityType.DECISION, "Adopt a queue", "f1")
        store.replace_source_evidence(ws, OWNER, doc(), Extraction("", (decision,), ()))
        node = source_entity_id(ws, OWNER, "doc1")
        did = eid(ws, EntityType.DECISION, "Adopt a queue")
        graph = walk(store, ws, [did], depth=1)
        decided = [e for e in graph.edges if e.kind is K.DECIDED_IN]
        assert [(e.target_id, e.evidence) for e in decided] == [(node, (Evidence("doc1", "f1"),))]
        entity = next(n for n in graph.nodes if n.id == did)
        assert (entity.status, entity.effective_at) == ("active", doc().created_at)

    def test_concurrent_replaces_sharing_an_entity_lose_no_evidence(self, store, ws) -> None:
        # A large graph makes each replace slow enough that unserialised writers overlap.
        bulk = tuple(ent(f"b{i}", T, f"Background topic {i}", "s1") for i in range(1500))
        store.upsert_extraction(ws, OWNER, meeting(1), Extraction("", bulk, ()))
        sources = [f"src{i}" for i in range(8)]
        start = threading.Barrier(len(sources))

        def write(source_id: str) -> None:
            shared = Extraction("", (ent("p", T, "Platform", f"{source_id}-f"),), ())
            start.wait()
            store.replace_source_evidence(ws, OWNER, doc(source_id), shared)

        with ThreadPoolExecutor(len(sources)) as pool:
            list(pool.map(write, sources))

        assert mentions(store, ws, eid(ws, T, "Platform")) == {
            Evidence(s, f"{s}-f") for s in sources
        }

    def test_a_meeting_is_never_written_as_a_source(self, store, ws) -> None:
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("t", T, "Queue", "s1"),), ())
        )
        ref = SourceRef("m1", "Meeting 1", datetime(2026, 10, 1, tzinfo=UTC), SourceKind.MEETING)
        with pytest.raises(ValueError):
            store.replace_source_evidence(ws, OWNER, ref, Extraction())
        assert store.evidence_segment_ids(ws, OWNER, "m1") == frozenset({"s1"})

    def test_a_later_mention_keeps_label_status_and_effective_date(self, store, ws) -> None:
        first = ent("d", EntityType.DECISION, "Adopt a Queue", "s1")
        store.upsert_extraction(ws, OWNER, meeting(1), Extraction("", (first,), ()))
        did = eid(ws, EntityType.DECISION, "Adopt a Queue")
        before = next(n for n in walk(store, ws, [did]).nodes if n.id == did)

        later = ent("d", EntityType.DECISION, "adopt a queue", "f1", status="draft")
        store.replace_source_evidence(ws, OWNER, doc(), Extraction("", (later,), ()))
        store.replace_source_evidence(ws, OWNER, doc(title="Renamed"), Extraction("", (later,), ()))

        after = next(n for n in walk(store, ws, [did]).nodes if n.id == did)
        assert after == before
        assert (after.label, after.status, after.effective_at) == (
            "Adopt a Queue",
            "active",
            meeting(1).created_at,
        )
        node = source_entity_id(ws, OWNER, "doc1")
        assert [n.label for n in walk(store, ws, [node]).nodes if n.id == node] == ["Renamed"]

    # --- the meetings-only view /v1 reads (legacy clients) -------------------------------------

    def test_hierarchy_proposals_never_crowd_meeting_proposals_out_of_the_inbox(
        self, store, ws
    ) -> None:
        people = tuple(ent(f"p{i}", EntityType.PERSON, f"Person {i}", "s1") for i in range(22))
        proposals = tuple(rel(f"p{i}", K.ABOUT, f"p{i + 1}", "s1") for i in range(21))
        store.upsert_extraction(ws, OWNER, meeting(1), Extraction("", people, proposals))
        service = GraphService(store)
        before = service.reviews(ws, OWNER, 20, meetings_only=True)
        assert len(before.edges) == 20

        topics = tuple(ent(f"t{i}", T, f"Concept {i}", "f1") for i in range(41))
        hierarchy = tuple(rel(f"t{i}", K.CHILD_OF, f"t{i + 1}", "f1") for i in range(40))
        store.replace_source_evidence(ws, OWNER, doc(), Extraction("", topics, hierarchy))

        assert service.reviews(ws, OWNER, 20, meetings_only=True) == before
        assert len(service.reviews(ws, OWNER, 200).edges) == 61  # the full view sees them all

    @pytest.mark.parametrize("depth", [1, 2])
    def test_documents_never_displace_meeting_nodes_from_an_entity_view(
        self, store, ws, depth
    ) -> None:
        for number in (1, 2, 3):
            people = (
                ent("s", T, "Shared topic", "s1"),
                ent("p", EntityType.PERSON, f"P{number}", "s1"),
            )
            store.upsert_extraction(
                ws, OWNER, meeting(number), Extraction("", people, (rel("p", K.ABOUT, "s", "s1"),))
            )
        service = GraphService(store)

        def view():
            return service.subgraph(
                ws,
                OWNER,
                seed_id=None,
                entity="Shared",
                depth=depth,
                include_proposed=True,
                as_of=None,
                limit=200,
                meetings_only=True,
            )

        before = view()
        assert sum(n.type is EntityType.MEETING for n in before.nodes) == 3
        for number in range(40):
            mention = (
                ent("s", T, "Shared topic", "f1"),
                ent("c", T, f"Doc concept {number}", "f1"),
            )
            store.replace_source_evidence(
                ws,
                OWNER,
                doc(f"doc{number}"),
                Extraction("", mention, (rel("c", K.CHILD_OF, "s", "f1"),)),
            )
        assert view() == before

    def test_one_document_leaves_the_overview_unchanged(self, store, ws) -> None:
        for number in range(1, 6):
            topics = (ent("a", T, f"Topic {number}a", "s1"), ent("b", T, f"Topic {number}b", "s1"))
            store.upsert_extraction(
                ws,
                OWNER,
                meeting(number),
                Extraction("", topics, (rel("a", K.DEPENDS_ON, "b", "s1"),)),
            )
        service = GraphService(store)

        def overview():
            return service.subgraph(
                ws,
                OWNER,
                seed_id=None,
                entity=None,
                depth=2,
                include_proposed=True,
                as_of=None,
                limit=60,
                meetings_only=True,
            )

        before = overview()
        concepts = tuple(ent(f"c{i}", T, f"Fresh concept {i}", "f1") for i in range(5))
        old = ent("o", T, "Topic 1a", "f2")
        store.replace_source_evidence(
            ws,
            OWNER,
            doc(),
            Extraction("", (*concepts, old), (rel("c0", K.DEPENDS_ON, "o", "f2"),)),
        )
        assert overview() == before
        assert service.changes(ws, OWNER, 10, meetings_only=True) == ()

    def test_the_meetings_only_view_hides_document_evidence_and_changes(self, store, ws) -> None:
        story = Extraction(
            "",
            (
                ent("old", EntityType.DECISION, "Keep cron", "s1"),
                ent("new", EntityType.DECISION, "Use a queue", "s2"),
            ),
            (rel("new", K.SUPERSEDES, "old", "s2"),),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), story)
        echo = Extraction("", story.entities, (rel("new", K.SUPERSEDES, "old", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("a-doc"), echo)  # sorts before "m1"
        alone = Extraction(
            "",
            (
                ent("x", EntityType.DECISION, "Old rule", "f1"),
                ent("y", EntityType.DECISION, "New rule", "f1"),
            ),
            (rel("y", K.SUPERSEDES, "x", "f1"),),
        )
        store.replace_source_evidence(
            ws, OWNER, doc("a-doc"), alone, dropped_fragment_ids=frozenset()
        )

        changes = store.recent_changes(ws, OWNER, 10, meetings_only=True)
        assert [(c.new_label, c.meeting_id) for c in changes] == [("Use a queue", "m1")]
        new = eid(ws, EntityType.DECISION, "Use a queue")
        graph = store.neighborhood(
            ws,
            OWNER,
            [new],
            depth=1,
            node_limit=50,
            fanout=50,
            include_proposed=True,
            as_of=None,
            meetings_only=True,
        )
        assert {e.source_id for edge in graph.edges for e in edge.evidence} == {"m1"}
        assert all(n.type is not EntityType.SOURCE for n in graph.nodes)
        assert store.find_entities(ws, OWNER, ["rule"], 10, meetings_only=True) == ()
        shown = {e.label for e in store.overview(ws, OWNER, 50, meetings_only=True)}
        assert shown == {"Keep cron", "Use a queue"}

    def test_review_through_the_meetings_only_view_refuses_hidden_relations(
        self, store, ws
    ) -> None:
        store.upsert_extraction(
            ws,
            OWNER,
            meeting(1),
            Extraction(
                "",
                (ent("a", T, "Ingestion", "s1"), ent("b", T, "Platform", "s1")),
                (rel("a", K.DEPENDS_ON, "b", "s1"),),
            ),
        )
        store.replace_source_evidence(ws, OWNER, doc(), HANDBOOK)
        child, parent = eid(ws, T, "Ingestion"), eid(ws, T, "Platform")
        node = source_entity_id(ws, OWNER, "doc1")
        hidden = [
            relation_id(ws, OWNER, child, K.CHILD_OF, parent),
            relation_id(ws, OWNER, child, K.MENTIONED_IN, node),
        ]
        for rid in hidden:
            with pytest.raises(NotFoundError):
                store.review_relation(ws, OWNER, rid, ReviewStatus.APPROVED, meetings_only=True)
        stored = {e.id: e.status for e in walk(store, ws, [child], depth=1).edges}
        assert [stored[rid] for rid in hidden] == [ReviewStatus.PROPOSED, ReviewStatus.APPROVED]

        depends = relation_id(ws, OWNER, child, K.DEPENDS_ON, parent)
        reviewed = store.review_relation(
            ws, OWNER, depends, ReviewStatus.APPROVED, meetings_only=True
        )
        assert reviewed.evidence == (Evidence("m1", "s1"),)

    def test_query_context_reads_meetings_only(self, store, ws) -> None:
        story = Extraction(
            "",
            (ent("a", T, "Ingestion", "s1"), ent("b", T, "Platform", "s1")),
            (rel("a", K.DEPENDS_ON, "b", "s1"),),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), story)
        store.review_relation(
            ws,
            OWNER,
            relation_id(ws, OWNER, eid(ws, T, "Ingestion"), K.DEPENDS_ON, eid(ws, T, "Platform")),
            ReviewStatus.APPROVED,
        )
        query = QueryService(None, None, store, None, None, 50)
        question = "how do ingestion and the handbook glossary relate"
        before = query._context_graph(ws, OWNER, question)

        glossary = Extraction(
            "",
            (ent("i", T, "Ingestion", "f1"), ent("g", T, "Handbook glossary", "f1")),
            (rel("g", K.DEPENDS_ON, "i", "f1"),),
        )
        store.replace_source_evidence(ws, OWNER, doc(), glossary)
        mid = relation_id(
            ws, OWNER, eid(ws, T, "Handbook glossary"), K.DEPENDS_ON, eid(ws, T, "Ingestion")
        )
        store.review_relation(ws, OWNER, mid, ReviewStatus.APPROVED)

        assert query._context_graph(ws, OWNER, question) == before
