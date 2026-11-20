"""Hierarchy reads (ADR-030): walks over CHILD_OF only, the pending parent links, one parent link
and the origin nodes a concept is mentioned in. The memory and SQLite graph stores run this suite
with their own `store`, `ws` and `raw_link` fixtures (test_hierarchy_memory.py,
test_hierarchy_sqlite.py); `raw_link(ws, child, parent)` writes an approved parent link behind the
guard's back, as corrupt data would be."""

from __future__ import annotations

import pytest

from nodo_api.domain.graph import (
    EntityType,
    Extraction,
    RelationKind,
    ReviewStatus,
    meeting_entity_id,
    relation_id,
    source_entity_id,
)
from nodo_api.errors import NotFoundError
from tests.graph_contract import OWNER, eid, ent, meeting, rel
from tests.source_graph_contract import doc

T = EntityType.TOPIC
K = RelationKind


def cid(ws: str, name: str, owner: str = OWNER) -> str:
    return eid(ws, T, f"Concept {name}", owner)


def link_id(ws: str, child: str, parent: str) -> str:
    return relation_id(ws, OWNER, cid(ws, child), K.CHILD_OF, cid(ws, parent))


def write(store, ws: str, pairs, source: str = "doc1", owner: str = OWNER) -> None:
    """One source proposing `child` under `parent` for each pair."""
    names = list(dict.fromkeys(n for pair in pairs for n in pair))
    extraction = Extraction(
        "",
        tuple(ent(n, T, f"Concept {n}", "f1") for n in names),
        tuple(rel(c, K.CHILD_OF, p, "f1") for c, p in pairs),
    )
    store.replace_source_evidence(ws, owner, doc(source), extraction)


def approve(store, ws: str, *pairs) -> None:
    for child, parent in pairs:
        store.review_relation(ws, OWNER, link_id(ws, child, parent), ReviewStatus.APPROVED)


def walk(store, ws, name, direction="both", depth=6, proposed=True, limit=100, owner=OWNER):
    return store.hierarchy(
        ws,
        owner,
        cid(ws, name),
        direction=direction,
        depth=depth,
        include_proposed=proposed,
        limit=limit,
    )


def names(ws: str, graph) -> tuple[list[str], set[tuple[str, str]]]:
    """The walk as concept names: nodes in order, edges as (child, parent)."""
    by_id = {cid(ws, n): n for n in "abcdefghijklmnopqrstuvwxyz"}
    nodes = [by_id[n.id] for n in graph.nodes]
    edges = {(by_id[e.source_id], by_id[e.target_id]) for e in graph.edges}
    return nodes, edges


class HierarchyContract:
    def test_parents_walk_returns_both_parents_of_a_concept(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("a", "c"), ("b", "d")])
        nodes, edges = names(ws, walk(store, ws, "a", "parents"))
        assert nodes[0] == "a"
        assert sorted(nodes) == ["a", "b", "c", "d"]
        assert edges == {("a", "b"), ("a", "c"), ("b", "d")}

    def test_children_walk_lists_a_shared_child_once_with_both_links(self, store, ws) -> None:
        write(store, ws, [("b", "a"), ("c", "a"), ("d", "b"), ("d", "c")])
        graph = walk(store, ws, "a", "children")
        nodes, edges = names(ws, graph)
        assert nodes[0] == "a"
        assert sorted(nodes) == ["a", "b", "c", "d"]
        assert edges == {("b", "a"), ("c", "a"), ("d", "b"), ("d", "c")}
        assert len(graph.edges) == 4
        assert all(e.evidence for e in graph.edges)

    def test_both_walks_up_and_down_but_not_to_siblings(self, store, ws) -> None:
        write(store, ws, [("a", "p"), ("s", "p"), ("c", "a")])
        nodes, edges = names(ws, walk(store, ws, "a", "both"))
        assert sorted(nodes) == ["a", "c", "p"]
        assert edges == {("a", "p"), ("c", "a")}

    def test_proposed_links_appear_only_when_asked(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("b", "c")])
        approve(store, ws, ("a", "b"))
        nodes, edges = names(ws, walk(store, ws, "a", "parents", proposed=False))
        assert nodes == ["a", "b"]
        assert edges == {("a", "b")}
        assert names(ws, walk(store, ws, "a", "parents"))[1] == {("a", "b"), ("b", "c")}

    def test_rejected_links_never_appear(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("a", "c")])
        store.review_relation(ws, OWNER, link_id(ws, "a", "b"), ReviewStatus.REJECTED)
        nodes, edges = names(ws, walk(store, ws, "a", "parents"))
        assert sorted(nodes) == ["a", "c"]
        assert edges == {("a", "c")}

    def test_depth_limits_the_walk_and_seven_is_a_value_error(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("b", "c"), ("c", "d")])
        nodes, edges = names(ws, walk(store, ws, "a", "parents", depth=2))
        assert sorted(nodes) == ["a", "b", "c"]
        assert edges == {("a", "b"), ("b", "c")}
        for depth in (0, 7):
            with pytest.raises(ValueError):
                walk(store, ws, "a", depth=depth)

    def test_walk_over_corrupt_cyclic_rows_terminates(self, store, ws, raw_link) -> None:
        write(store, ws, [("a", "b"), ("b", "c")])
        raw_link(ws, cid(ws, "c"), cid(ws, "a"))  # c under a closes a cycle the guard refuses
        for direction in ("parents", "children", "both"):
            graph = walk(store, ws, "a", direction)
            nodes, edges = names(ws, graph)
            assert sorted(nodes) == ["a", "b", "c"]
            assert len(nodes) == len(set(nodes))
            assert edges == {("a", "b"), ("b", "c"), ("c", "a")}
            assert len(graph.edges) == 3

    def test_walk_skips_a_dangling_endpoint(self, store, ws, raw_link) -> None:
        write(store, ws, [("a", "b")])
        raw_link(ws, cid(ws, "a"), "missing-entity")  # a link to an entity that is not there
        graph = walk(store, ws, "a", "parents")
        assert [n.id for n in graph.nodes] == [cid(ws, "a"), cid(ws, "b")]

    def test_pending_hierarchy_ignores_links_without_evidence(self, store, ws, raw_link) -> None:
        write(store, ws, [("a", "b"), ("c", "d")])
        raw_link(ws, cid(ws, "d"), cid(ws, "a"), ReviewStatus.PROPOSED, evidence=False)
        pending = store.pending_hierarchy(ws, OWNER, 10)
        assert {e.id for e in pending.edges} == {link_id(ws, "a", "b"), link_id(ws, "c", "d")}

    def test_pending_hierarchy_orders_one_write_by_id(self, store, ws) -> None:
        pairs = [("a", "b"), ("c", "d"), ("e", "f"), ("g", "h")]
        write(store, ws, pairs)
        expected = sorted(link_id(ws, c, p) for c, p in pairs)
        assert [e.id for e in store.pending_hierarchy(ws, OWNER, 10).edges] == expected

    def test_limit_sets_truncated(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("b", "c"), ("c", "d")])
        graph = walk(store, ws, "a", "parents", limit=2)
        nodes, edges = names(ws, graph)
        assert nodes == ["a", "b"]
        assert edges == {("a", "b")}
        assert graph.truncated
        assert not walk(store, ws, "a", "parents", limit=4).truncated

    def test_origin_node_or_other_owner_is_not_found(self, store, ws) -> None:
        write(store, ws, [("a", "b")])
        source_node = source_entity_id(ws, OWNER, "doc1")
        with pytest.raises(NotFoundError):
            store.hierarchy(
                ws,
                OWNER,
                source_node,
                direction="both",
                depth=1,
                include_proposed=True,
                limit=10,
            )
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("x", T, "Concept x", "s1"),), ())
        )
        with pytest.raises(NotFoundError):
            store.hierarchy(
                ws,
                OWNER,
                meeting_entity_id(ws, OWNER, "m1"),
                direction="both",
                depth=1,
                include_proposed=True,
                limit=10,
            )
        with pytest.raises(NotFoundError):
            walk(store, ws, "a", owner="bob")
        with pytest.raises(NotFoundError):
            walk(store, ws, "z")

    def test_pending_hierarchy_lists_only_proposed_child_of_newest_first(self, store, ws) -> None:
        write(store, ws, [("a", "b"), ("c", "d")], source="doc1")
        write(store, ws, [("e", "f")], source="doc2")
        approve(store, ws, ("c", "d"))
        extraction = Extraction(
            "",
            (ent("p", T, "Concept p", "f1"), ent("q", T, "Concept q", "f1")),
            (rel("p", K.DEPENDS_ON, "q", "f1"),),
        )
        store.replace_source_evidence(ws, OWNER, doc("doc3"), extraction)
        pending = store.pending_hierarchy(ws, OWNER, 10)
        assert [e.id for e in pending.edges] == [link_id(ws, "e", "f"), link_id(ws, "a", "b")]
        assert all(e.kind is K.CHILD_OF and e.evidence for e in pending.edges)
        assert {n.id for n in pending.nodes} == {cid(ws, n) for n in "abef"}
        assert not pending.truncated
        first = store.pending_hierarchy(ws, OWNER, 1)
        assert [e.id for e in first.edges] == [link_id(ws, "e", "f")]
        assert first.truncated
        assert store.pending_hierarchy(ws, "bob", 10).edges == ()

    def test_child_of_refuses_other_kinds_and_other_owners(self, store, ws) -> None:
        extraction = Extraction(
            "",
            (ent("a", T, "Concept a", "f1"), ent("b", T, "Concept b", "f1")),
            (rel("a", K.CHILD_OF, "b", "f1"), rel("a", K.DEPENDS_ON, "b", "f1")),
        )
        store.replace_source_evidence(ws, OWNER, doc(), extraction)
        found = store.child_of(ws, OWNER, link_id(ws, "a", "b"))
        assert (found.source_id, found.target_id) == (cid(ws, "a"), cid(ws, "b"))
        assert found.kind is K.CHILD_OF and found.evidence
        other_kind = relation_id(ws, OWNER, cid(ws, "a"), K.DEPENDS_ON, cid(ws, "b"))
        for owner, rid in (
            (OWNER, other_kind),
            ("bob", link_id(ws, "a", "b")),
            (OWNER, "missing"),
        ):
            with pytest.raises(NotFoundError):
                store.child_of(ws, owner, rid)

    def test_mentions_lists_source_and_meeting_nodes(self, store, ws) -> None:
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("a", T, "Concept a", "s1"),), ())
        )
        write(store, ws, [("a", "b")])
        found = store.mentions(ws, OWNER, cid(ws, "a"), 10)
        assert [(n.type, n.meeting_id, n.source_id) for n in found] == [
            (EntityType.SOURCE, None, "doc1"),  # created 2026-11-01
            (EntityType.MEETING, "m1", None),  # created 2026-10-01
        ]
        assert [n.source_id for n in store.mentions(ws, OWNER, cid(ws, "a"), 1)] == ["doc1"]
        assert store.mentions(ws, OWNER, cid(ws, "b"), 10)[0].source_id == "doc1"
        assert store.mentions(ws, "bob", cid(ws, "a"), 10) == ()

    def test_workspace_isolation(self, store, ws) -> None:
        other = f"{ws}-other"
        write(store, ws, [("a", "b")])
        write(store, other, [("a", "c")])
        assert names(ws, walk(store, ws, "a"))[1] == {("a", "b")}
        assert names(other, walk(store, other, "a"))[1] == {("a", "c")}
        assert [e.id for e in store.pending_hierarchy(ws, OWNER, 10).edges] == [
            link_id(ws, "a", "b")
        ]
        with pytest.raises(NotFoundError):
            store.child_of(other, OWNER, link_id(ws, "a", "b"))
        assert {n.source_id for n in store.mentions(other, OWNER, cid(ws, "a"), 10)} == set()
