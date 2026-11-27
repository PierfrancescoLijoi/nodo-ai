"""Every CHILD_OF write is guarded (ADR-030): the non-rejected parent links stay acyclic and no path
is longer than six links. The memory and SQLite graph stores run this suite with their own `store`
and `ws` fixtures (test_hierarchy_guard_memory.py, test_hierarchy_guard_sqlite.py)."""

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from nodo_api.domain.graph import EntityType, Extraction, RelationKind, ReviewStatus, relation_id
from nodo_api.errors import CycleRefused, DepthRefused
from tests.graph_contract import OWNER, eid, ent, rel, walk
from tests.source_graph_contract import doc

T = EntityType.TOPIC
K = RelationKind


def topics(*names: str, fragment: str = "f1"):
    return tuple(ent(n, T, f"Concept {n}", fragment) for n in names)


def link_id(ws: str, child: str, parent: str) -> str:
    return relation_id(
        ws, OWNER, eid(ws, T, f"Concept {child}"), K.CHILD_OF, eid(ws, T, f"Concept {parent}")
    )


def links(store, ws: str) -> dict[tuple[str, str], ReviewStatus]:
    """Every stored CHILD_OF, as (child key, parent key) -> status."""
    names = {eid(ws, T, f"Concept {n}"): n for n in "abcdefghijklmnopqrstuvwxyz"}
    names |= {eid(ws, T, f"Concept c{i}"): f"c{i}" for i in range(10)}
    graph = walk(store, ws, list(names), depth=1, node_limit=500, fanout=500)
    return {
        (names[e.source_id], names[e.target_id]): e.status
        for e in graph.edges
        if e.kind is K.CHILD_OF
    }


def chain_extraction(length: int, fragment: str = "f1") -> Extraction:
    """c0 under c1 under ... under c<length>."""
    names = [f"c{i}" for i in range(length + 1)]
    return Extraction(
        "",
        topics(*names, fragment=fragment),
        tuple(rel(f"c{i}", K.CHILD_OF, f"c{i + 1}", fragment) for i in range(length)),
    )


class HierarchyGuardContract:
    def test_extraction_link_that_closes_a_cycle_is_not_stored(self, store, ws) -> None:
        extraction = Extraction(
            "",
            topics("a", "b", "c"),
            (
                rel("a", K.CHILD_OF, "b", "f1"),
                rel("b", K.CHILD_OF, "c", "f1"),
                rel("c", K.CHILD_OF, "a", "f1"),
            ),
        )
        store.replace_source_evidence(ws, OWNER, doc(), extraction)
        assert set(links(store, ws)) == {("a", "b"), ("b", "c")}

    def test_extraction_link_beyond_depth_six_is_not_stored_and_its_evidence_neither(
        self, store, ws
    ) -> None:
        chain = chain_extraction(6)
        extraction = Extraction(
            "",
            (*chain.entities, *topics("x")),
            (*chain.relations, rel("x", K.CHILD_OF, "c0", "f9")),
        )
        store.replace_source_evidence(ws, OWNER, doc(), extraction)
        assert set(links(store, ws)) == {(f"c{i}", f"c{i + 1}") for i in range(6)}
        assert "f9" not in store.evidence_segment_ids(ws, OWNER, "doc1")

    def test_two_parents_from_one_extraction_are_both_proposed(self, store, ws) -> None:
        extraction = Extraction(
            "",
            topics("a", "b", "c"),
            (rel("a", K.CHILD_OF, "b", "f1"), rel("a", K.CHILD_OF, "c", "f1")),
        )
        store.replace_source_evidence(ws, OWNER, doc(), extraction)
        assert links(store, ws) == {
            ("a", "b"): ReviewStatus.PROPOSED,
            ("a", "c"): ReviewStatus.PROPOSED,
        }

    def test_rejected_links_do_not_block_new_ones(self, store, ws) -> None:
        first = Extraction("", topics("a", "b"), (rel("a", K.CHILD_OF, "b", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("doc1"), first)
        store.review_relation(ws, OWNER, link_id(ws, "a", "b"), ReviewStatus.REJECTED)
        second = Extraction("", topics("a", "b"), (rel("b", K.CHILD_OF, "a", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("doc2"), second)
        assert links(store, ws)[("b", "a")] is ReviewStatus.PROPOSED

    def test_reapproving_a_rejected_link_that_now_closes_a_cycle_is_409_cycle(
        self, store, ws
    ) -> None:
        first = Extraction("", topics("a", "b"), (rel("a", K.CHILD_OF, "b", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("doc1"), first)
        store.review_relation(ws, OWNER, link_id(ws, "a", "b"), ReviewStatus.REJECTED)
        second = Extraction("", topics("a", "b"), (rel("b", K.CHILD_OF, "a", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("doc2"), second)

        with pytest.raises(CycleRefused) as refused:
            store.review_relation(ws, OWNER, link_id(ws, "a", "b"), ReviewStatus.APPROVED)
        assert refused.value.code == "would_create_cycle"
        assert refused.value.status_code == 409
        assert ("a", "b") not in links(store, ws)  # still rejected: walks never show it

    def test_reapproving_a_rejected_link_that_is_now_too_deep_is_409_too_deep(
        self, store, ws
    ) -> None:
        first = Extraction("", topics("x", "c0"), (rel("x", K.CHILD_OF, "c0", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc("doc1"), first)
        store.review_relation(ws, OWNER, link_id(ws, "x", "c0"), ReviewStatus.REJECTED)
        store.replace_source_evidence(ws, OWNER, doc("doc2"), chain_extraction(6))

        with pytest.raises(DepthRefused) as refused:
            store.review_relation(ws, OWNER, link_id(ws, "x", "c0"), ReviewStatus.APPROVED)
        assert refused.value.code == "too_deep"
        assert refused.value.status_code == 409
        assert ("x", "c0") not in links(store, ws)

    def test_proposed_to_approved_needs_no_recheck(self, store, ws, monkeypatch) -> None:
        store.replace_source_evidence(ws, OWNER, doc(), chain_extraction(6))

        def boom(*_args, **_kwargs):
            raise AssertionError("a proposed link is approved without a check")

        monkeypatch.setattr(sys.modules[type(store).__module__], "check_link", boom)
        approved = store.review_relation(ws, OWNER, link_id(ws, "c0", "c1"), ReviewStatus.APPROVED)
        assert approved.status is ReviewStatus.APPROVED

    def test_child_of_is_never_auto_approved(self, store, ws) -> None:
        extraction = Extraction("", topics("a", "b"), (rel("a", K.CHILD_OF, "b", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc(), extraction)
        assert links(store, ws) == {("a", "b"): ReviewStatus.PROPOSED}

    def test_concurrent_opposite_links_never_form_a_cycle(self, store, ws, monkeypatch) -> None:
        module = sys.modules[type(store).__module__]
        real = module.check_link
        first_inside, second_calling, second_checked = (threading.Event() for _ in range(3))
        overlapped: list[bool] = []
        guard = threading.Lock()

        def spy(*args, **kwargs):
            with guard:
                first = not first_inside.is_set()
                first_inside.set()
            if first:
                # The first writer holds the write lock here; the second has called the store.
                assert second_calling.wait(5)
                overlapped.append(second_checked.wait(0.2))  # must time out: it is blocked
            else:
                second_checked.set()
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "check_link", spy)

        def write(source_id: str, child: str, parent: str) -> None:
            extraction = Extraction("", topics("a", "b"), (rel(child, K.CHILD_OF, parent, "f1"),))
            if source_id == "doc2":
                second_calling.set()
            store.replace_source_evidence(ws, OWNER, doc(source_id), extraction)

        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(write, "doc1", "a", "b")
            assert first_inside.wait(5)
            second = pool.submit(write, "doc2", "b", "a")
            first.result(10)
            second.result(10)

        assert overlapped == [False]  # the second check never ran inside the first write
        assert second_checked.is_set()
        assert links(store, ws) == {("a", "b"): ReviewStatus.PROPOSED}

    def test_a_reviewed_link_survives_a_reverse_proposal_listed_first(self, store, ws) -> None:
        first = Extraction("", topics("a", "b"), (rel("a", K.CHILD_OF, "b", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc(), first)
        store.review_relation(ws, OWNER, link_id(ws, "a", "b"), ReviewStatus.APPROVED)
        again = Extraction(
            "",
            topics("a", "b", fragment="f2"),
            (rel("b", K.CHILD_OF, "a", "f2"), rel("a", K.CHILD_OF, "b", "f2")),
        )
        store.replace_source_evidence(
            ws, OWNER, doc(), again, dropped_fragment_ids=frozenset({"f1"})
        )
        assert links(store, ws) == {("a", "b"): ReviewStatus.APPROVED}

    def test_reimport_that_reverses_a_link_keeps_the_new_direction(self, store, ws) -> None:
        first = Extraction("", topics("a", "b"), (rel("a", K.CHILD_OF, "b", "f1"),))
        store.replace_source_evidence(ws, OWNER, doc(), first)
        again = Extraction("", topics("a", "b", fragment="f2"), (rel("b", K.CHILD_OF, "a", "f2"),))
        store.replace_source_evidence(
            ws, OWNER, doc(), again, dropped_fragment_ids=frozenset({"f1"})
        )
        assert links(store, ws) == {("b", "a"): ReviewStatus.PROPOSED}

    def test_refusal_log_has_one_line_per_code_and_no_label(self, store, ws, caplog) -> None:
        store.replace_source_evidence(ws, OWNER, doc("doc1"), chain_extraction(6))
        hostile = Extraction(
            "",
            (*chain_extraction(6, "f2").entities, *topics("x", fragment="f2")),
            (
                rel("c1", K.CHILD_OF, "c0", "f2"),
                rel("c2", K.CHILD_OF, "c0", "f2"),
                rel("x", K.CHILD_OF, "c0", "f2"),
            ),
        )
        with caplog.at_level(logging.INFO):
            store.replace_source_evidence(ws, OWNER, doc("doc2"), hostile)

        refused = [r for r in caplog.records if "hierarchy_link_refused" in r.getMessage()]
        assert sorted(r.getMessage() for r in refused) == [
            "hierarchy_link_refused code=cycle count=2",
            "hierarchy_link_refused code=too_deep count=1",
        ]
        for record in caplog.records:
            assert "Concept" not in record.getMessage()
            assert "Concept" not in repr(record.args)
