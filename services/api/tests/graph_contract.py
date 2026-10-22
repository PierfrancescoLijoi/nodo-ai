"""The behaviour every GraphStore must have. The in-memory store and the PostgreSQL store both run
this suite (test_graph_memory.py, tests/azure/test_postgres.py), so they can not drift apart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.domain.graph import (
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    Extraction,
    MeetingRef,
    RelationKind,
    ReviewStatus,
    entity_id,
    meeting_entity_id,
    normalize_label,
)
from nodo_api.errors import NotFoundError

OWNER = "alice"
P, T, D, A = EntityType.PERSON, EntityType.TOPIC, EntityType.DECISION, EntityType.ACTION
K = RelationKind


def ent(key: str, kind: EntityType, label: str, *segments: str, **extra) -> ExtractedEntity:
    return ExtractedEntity(key, kind, label, tuple(segments), **extra)


def rel(source: str, kind: RelationKind, target: str, *segments: str, confidence: float = 0.9):
    return ExtractedRelation(source, target, kind, confidence, tuple(segments))


def meeting(number: int, title: str | None = None) -> MeetingRef:
    return MeetingRef(
        f"m{number}", title or f"Meeting {number}", datetime(2026, 10, number, tzinfo=UTC)
    )


def eid(ws: str, kind: EntityType, label: str, owner: str = OWNER) -> str:
    return entity_id(ws, owner, kind, normalize_label(label))


def walk(
    store, ws, seeds, *, depth=2, proposed=True, node_limit=100, fanout=50, as_of=None, owner=OWNER
):
    return store.neighborhood(
        ws,
        owner,
        seeds,
        depth=depth,
        node_limit=node_limit,
        fanout=fanout,
        include_proposed=proposed,
        as_of=as_of,
    )


def edge_kinds(graph) -> set[tuple[str, str, str]]:
    names = {n.id: n.label for n in graph.nodes}
    return {(names[e.source_id], e.kind.value, names[e.target_id]) for e in graph.edges}


def queue_story() -> tuple[Extraction, Extraction]:
    first = Extraction(
        "Marco proposed a synchronous flow.",
        (
            ent("p", P, "Marco", "s1"),
            ent("t", T, "Ingestion", "s1"),
            ent("d", D, "Keep the flow synchronous", "s1"),
        ),
        (rel("d", K.PROPOSED_BY, "p", "s1"), rel("d", K.ABOUT, "t", "s1")),
    )
    return first, Extraction("", (), ())


class GraphContract:
    """Subclasses provide the `store` and `ws` fixtures."""

    # ------------------------------------------------------------------ writing

    def test_an_extraction_becomes_entities_and_relations(self, store, ws) -> None:
        first, _ = queue_story()
        result = store.upsert_extraction(ws, OWNER, meeting(1), first)
        assert result.entities == 4  # three extracted plus the meeting
        assert result.relations == 6  # two inferred, three mentions, one decided-in
        graph = walk(store, ws, [eid(ws, P, "Marco")], depth=2)
        assert ("Keep the flow synchronous", "PROPOSED_BY", "Marco") in edge_kinds(graph)
        assert ("Marco", "MENTIONED_IN", "Meeting 1") in edge_kinds(graph)
        assert all(e.evidence for e in graph.edges)

    def test_what_the_model_infers_waits_for_review_but_mentions_do_not(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        seed = [eid(ws, P, "Marco")]
        default = walk(store, ws, seed, depth=1, proposed=False)
        assert edge_kinds(default) == {("Marco", "MENTIONED_IN", "Meeting 1")}
        with_proposed = walk(store, ws, seed, depth=1, proposed=True)
        assert {e.status for e in with_proposed.edges} == {
            ReviewStatus.APPROVED,
            ReviewStatus.PROPOSED,
        }

    def test_running_the_same_extraction_twice_changes_nothing(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        once = walk(store, ws, [eid(ws, P, "Marco")])
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        twice = walk(store, ws, [eid(ws, P, "Marco")])
        assert {n.id for n in once.nodes} == {n.id for n in twice.nodes}
        assert {e.id for e in once.edges} == {e.id for e in twice.edges}
        assert [e.evidence for e in sorted(once.edges, key=lambda e: e.id)] == [
            e.evidence for e in sorted(twice.edges, key=lambda e: e.id)
        ]

    def test_the_same_thing_in_another_spelling_is_one_entity(self, store, ws) -> None:
        store.upsert_extraction(
            ws, OWNER, meeting(1), Extraction("", (ent("p", P, "Łukasz Kowalski", "s1"),))
        )
        store.upsert_extraction(
            ws, OWNER, meeting(2), Extraction("", (ent("p", P, "lukasz  KOWALSKI", "s9"),))
        )
        found = store.find_entities(ws, OWNER, ["lukasz"], 10)
        assert len(found) == 1
        graph = walk(store, ws, [found[0].id], depth=1, proposed=False)
        assert {e.target_id for e in graph.edges} == {
            meeting_entity_id(ws, OWNER, "m1"),
            meeting_entity_id(ws, OWNER, "m2"),
        }

    def test_two_people_with_the_same_first_name_stay_apart(self, store, ws) -> None:
        extraction = Extraction(
            "", (ent("a", P, "Marco Rossi", "s1"), ent("b", P, "Marco Bianchi", "s1"))
        )
        store.upsert_extraction(ws, OWNER, meeting(1), extraction)
        assert {e.label for e in store.find_entities(ws, OWNER, ["marco"], 10)} == {
            "Marco Rossi",
            "Marco Bianchi",
        }

    def test_review_decisions_survive_a_new_extraction(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        graph = walk(store, ws, [eid(ws, P, "Marco")])
        proposed = next(e for e in graph.edges if e.kind is K.PROPOSED_BY)
        approved = store.review_relation(ws, OWNER, proposed.id, ReviewStatus.APPROVED)
        assert approved.status is ReviewStatus.APPROVED and approved.evidence
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        again = walk(store, ws, [eid(ws, P, "Marco")], proposed=False)
        assert any(e.id == proposed.id for e in again.edges)

    def test_a_rejected_relation_stays_gone_after_re_extraction(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        graph = walk(store, ws, [eid(ws, P, "Marco")])
        about = next(e for e in graph.edges if e.kind is K.ABOUT)
        store.review_relation(ws, OWNER, about.id, ReviewStatus.REJECTED)
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        assert all(e.id != about.id for e in walk(store, ws, [eid(ws, P, "Marco")]).edges)

    def test_re_extraction_drops_what_the_new_run_no_longer_finds(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        smaller = Extraction("", (ent("p", P, "Marco", "s1"),))
        store.upsert_extraction(ws, OWNER, meeting(1), smaller)
        assert store.find_entities(ws, OWNER, ["synchronous"], 10) == ()
        assert [e.label for e in store.find_entities(ws, OWNER, ["marco"], 10)] == ["Marco"]

    def test_review_input_is_checked(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        edge = walk(store, ws, [eid(ws, P, "Marco")]).edges[0]
        with pytest.raises(ValueError):
            store.review_relation(ws, OWNER, edge.id, ReviewStatus.PROPOSED)
        with pytest.raises(NotFoundError):
            store.review_relation(
                ws, OWNER, "00000000-0000-0000-0000-000000000000", ReviewStatus.APPROVED
            )

    # ----------------------------------------------------------------- erasing

    def test_erasing_a_meeting_removes_what_only_it_knew(self, store, ws) -> None:
        store.upsert_extraction(
            ws,
            OWNER,
            meeting(1),
            Extraction("", (ent("p", P, "Marco", "s1"), ent("t", T, "Only here", "s1"))),
        )
        store.upsert_extraction(
            ws, OWNER, meeting(2), Extraction("", (ent("p", P, "Marco", "s2"),))
        )
        store.erase(ws, "m1")
        assert store.find_entities(ws, OWNER, ["only here"], 10) == ()
        marco = store.find_entities(ws, OWNER, ["marco"], 10)
        assert len(marco) == 1  # still known from meeting 2
        graph = walk(store, ws, [marco[0].id], depth=1, proposed=False)
        assert {e.evidence[0].meeting_id for e in graph.edges} == {"m2"}
        store.erase(ws, "m1")  # idempotent

    def test_nothing_survives_from_a_deleted_meeting_even_when_another_points_at_it(
        self, store, ws
    ) -> None:
        old, _ = self._supersession(store, ws)
        store.erase(ws, "m1")  # the replaced decision text came from meeting 1
        assert store.find_entities(ws, OWNER, ["synchronous"], 10) == ()
        new = eid(ws, D, "Use an async queue")
        graph = walk(store, ws, [new], depth=2)
        assert {e.label for e in graph.nodes} >= {"Use an async queue", "Giulia"}
        assert all(e.kind is not K.SUPERSEDES for e in graph.edges)
        assert old not in {n.id for n in graph.nodes}

    def test_erasing_every_meeting_leaves_an_empty_graph(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        store.erase(ws, "m1")
        assert store.overview(ws, OWNER, 10) == ()
        assert store.meeting_digests(ws, OWNER, ["m1"])["m1"].people == ()

    # ------------------------------------------------------------ supersession

    def _supersession(self, store, ws):
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        old = eid(ws, D, "Keep the flow synchronous")
        second = Extraction(
            "",
            (ent("d", D, "Use an async queue", "s5"), ent("p", P, "Giulia", "s5")),
            (rel("d", K.SUPERSEDES, f"existing:{old}", "s5"), rel("d", K.PROPOSED_BY, "p", "s5")),
        )
        store.upsert_extraction(ws, OWNER, meeting(2), second)
        graph = walk(store, ws, [eid(ws, D, "Use an async queue")], depth=1)
        return old, next(e for e in graph.edges if e.kind is K.SUPERSEDES)

    def _status(self, store, ws, decision_id):
        graph = walk(store, ws, [decision_id], depth=1)
        return next(n.status for n in graph.nodes if n.id == decision_id)

    def test_a_decision_is_replaced_only_after_a_person_approves(self, store, ws) -> None:
        old, supersedes = self._supersession(store, ws)
        assert supersedes.target_id == old and supersedes.status is ReviewStatus.PROPOSED
        assert self._status(store, ws, old) == "active"
        store.review_relation(ws, OWNER, supersedes.id, ReviewStatus.APPROVED)
        assert self._status(store, ws, old) == "superseded"
        store.review_relation(ws, OWNER, supersedes.id, ReviewStatus.REJECTED)
        assert self._status(store, ws, old) == "active"

    def test_erasing_the_replacing_meeting_brings_the_old_decision_back(self, store, ws) -> None:
        old, supersedes = self._supersession(store, ws)
        store.review_relation(ws, OWNER, supersedes.id, ReviewStatus.APPROVED)
        assert self._status(store, ws, old) == "superseded"
        store.erase(ws, "m2")
        assert self._status(store, ws, old) == "active"

    def test_recent_changes_list_replacements_newest_first(self, store, ws) -> None:
        _, supersedes = self._supersession(store, ws)
        changes = store.recent_changes(ws, OWNER, 5)
        assert [(c.new_label, c.old_label, c.meeting_id) for c in changes] == [
            ("Use an async queue", "Keep the flow synchronous", "m2")
        ]
        store.review_relation(ws, OWNER, supersedes.id, ReviewStatus.REJECTED)
        assert store.recent_changes(ws, OWNER, 5) == ()

    def test_an_approved_replacement_starts_when_it_was_approved(self, store, ws) -> None:
        _, supersedes = self._supersession(store, ws)
        store.review_relation(ws, OWNER, supersedes.id, ReviewStatus.APPROVED)
        seed = [eid(ws, D, "Use an async queue")]
        past = walk(store, ws, seed, depth=1, as_of=datetime.now(UTC) - timedelta(days=1))
        future = walk(store, ws, seed, depth=1, as_of=datetime.now(UTC) + timedelta(days=1))
        assert not any(e.kind is K.SUPERSEDES for e in past.edges)
        assert any(e.kind is K.SUPERSEDES for e in future.edges)

    # --------------------------------------------------------------- reading

    def test_traversal_is_bounded_by_depth(self, store, ws) -> None:
        chain = Extraction(
            "",
            tuple(ent(f"t{i}", T, f"Topic {i}", "s1") for i in range(4)),
            tuple(rel(f"t{i}", K.DEPENDS_ON, f"t{i + 1}", "s1") for i in range(3)),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), chain)
        seed = [eid(ws, T, "Topic 0")]

        def chain_edges(depth: int) -> set[tuple[str, str]]:
            graph = walk(store, ws, seed, depth=depth)
            names = {n.id: n.label for n in graph.nodes}
            return {
                (names[e.source_id], names[e.target_id])
                for e in graph.edges
                if e.kind is K.DEPENDS_ON
            }

        assert chain_edges(1) == {("Topic 0", "Topic 1")}
        assert chain_edges(2) == {
            ("Topic 0", "Topic 1"),
            ("Topic 1", "Topic 2"),
        }  # not Topic 2 -> 3

    def test_a_hub_is_cut_at_its_fanout_and_reported(self, store, ws) -> None:
        star = Extraction(
            "",
            (
                ent("hub", T, "Hub", "s1"),
                *(ent(f"l{i}", T, f"Leaf {i:02d}", "s1") for i in range(40)),
            ),
            tuple(
                rel("hub", K.DEPENDS_ON, f"l{i}", "s1", confidence=0.5 + i / 100) for i in range(40)
            ),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), star)
        hub = [eid(ws, T, "Hub")]
        cut = walk(store, ws, hub, depth=1, fanout=10, proposed=True)
        assert cut.truncated is True
        assert len(cut.edges) == 10  # the mention (confidence 1.0) plus the nine best leaves
        best = {e.target_id for e in cut.edges if e.kind is K.DEPENDS_ON}
        assert (
            eid(ws, T, "Leaf 39") in best and eid(ws, T, "Leaf 00") not in best
        )  # highest confidence wins
        full = walk(store, ws, hub, depth=1, fanout=100, proposed=True)
        assert full.truncated is False

    def test_the_node_cap_holds_and_is_reported(self, store, ws) -> None:
        star = Extraction(
            "",
            (ent("hub", T, "Hub", "s1"), *(ent(f"l{i}", T, f"Leaf {i}", "s1") for i in range(20))),
            tuple(rel("hub", K.DEPENDS_ON, f"l{i}", "s1") for i in range(20)),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), star)
        graph = walk(store, ws, [eid(ws, T, "Hub")], depth=2, node_limit=6, proposed=True)
        assert len(graph.nodes) == 6 and graph.truncated is True
        ids = {n.id for n in graph.nodes}
        assert all(e.source_id in ids and e.target_id in ids for e in graph.edges)

    def test_unknown_seeds_give_an_empty_graph(self, store, ws) -> None:
        graph = walk(store, ws, ["00000000-0000-0000-0000-000000000000"])
        assert graph.nodes == () and graph.edges == () and graph.truncated is False

    def test_each_owner_and_workspace_has_a_private_graph(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        marco = eid(ws, P, "Marco")
        assert store.find_entities(ws, "bob", ["marco"], 10) == ()
        assert walk(store, ws, [marco], owner="bob").nodes == ()
        assert walk(store, ws + "-other", [marco]).nodes == ()
        edge = walk(store, ws, [marco]).edges[0]
        with pytest.raises(NotFoundError):
            store.review_relation(ws, "bob", edge.id, ReviewStatus.APPROVED)

    def test_finding_entities_ranks_by_matches_and_escapes_wildcards(self, store, ws) -> None:
        store.upsert_extraction(
            ws,
            OWNER,
            meeting(1),
            Extraction(
                "",
                (
                    ent("a", T, "Async queue", "s1"),
                    ent("b", T, "Queue", "s1"),
                    ent("c", T, "100% done_ok", "s1"),
                    ent("d", P, "Sara", "s1"),
                ),
            ),
        )
        found = store.find_entities(ws, OWNER, ["async", "queue"], 10)
        assert [e.label for e in found][:2] == ["Async queue", "Queue"]
        assert [
            e.label for e in store.find_entities(ws, OWNER, ["queue"], 10, frozenset({P}))
        ] == []
        assert [e.label for e in store.find_entities(ws, OWNER, ["100%"], 10)] == ["100% done_ok"]
        assert store.find_entities(ws, OWNER, ["%"], 10) == ()  # too short to be a term
        assert store.find_entities(ws, OWNER, ["zzz"], 10) == ()

    def test_overview_and_recent_decisions_are_newest_first(self, store, ws) -> None:
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        store.upsert_extraction(
            ws, OWNER, meeting(2), Extraction("", (ent("d", D, "Second decision", "s1"),))
        )
        assert store.recent_decisions(ws, OWNER, 5)[0].label == "Second decision"
        assert store.overview(ws, OWNER, 1)[0].label == "Second decision"
        assert all(e.type is not EntityType.MEETING for e in store.overview(ws, OWNER, 20))

    def test_meeting_digests_group_what_a_meeting_produced(self, store, ws) -> None:
        extraction = Extraction(
            "",
            (
                ent("p", P, "Sara", "s1"),
                ent("t", T, "Benchmark", "s1"),
                ent("d", D, "Use retries", "s1"),
                ent("a", A, "Write the benchmark", "s2", due_at=datetime(2026, 10, 28, tzinfo=UTC)),
            ),
            (rel("a", K.ASSIGNED_TO, "p", "s2"),),
        )
        store.upsert_extraction(ws, OWNER, meeting(1), extraction)
        digest = store.meeting_digests(ws, OWNER, ["m1", "m-unknown"])
        one = digest["m1"]
        assert [e.label for e in one.people] == ["Sara"]
        assert [e.label for e in one.topics] == ["Benchmark"]
        assert [e.label for e in one.decisions] == ["Use retries"]
        action = one.actions[0]
        assert one.action_owners[action.id] == "Sara"
        assert action.due_at == datetime(2026, 10, 28, tzinfo=UTC)
        assert one.review[one.decisions[0].id] == "proposed"
        assert digest["m-unknown"].decisions == ()
        approved = next(e for e in walk(store, ws, [action.id]).edges if e.kind is K.ASSIGNED_TO)
        store.review_relation(ws, OWNER, approved.id, ReviewStatus.APPROVED)
        assert store.meeting_digests(ws, OWNER, ["m1"])["m1"].review[action.id] == "approved"
