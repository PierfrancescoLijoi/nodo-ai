"""The inbox of proposals a person has not decided yet: what every GraphStore must do (ADR-025).
Mixed into the contract classes of the memory, SQLite and PostgreSQL stores."""

from __future__ import annotations

from nodo_api.domain.graph import RelationKind, ReviewStatus
from tests.graph_contract import OWNER, meeting, queue_story


class PendingReviewContract:
    """Subclasses provide the `store` and `ws` fixtures."""

    def _story(self, store, ws):
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)

    def test_the_proposals_the_model_made_wait_in_the_inbox_with_their_ends_and_evidence(
        self, store, ws
    ) -> None:
        self._story(store, ws)
        inbox = store.pending_review(ws, OWNER, 10)
        assert {e.kind for e in inbox.edges} == {
            RelationKind.PROPOSED_BY,
            RelationKind.ABOUT,
            RelationKind.DECIDED_IN,  # a decision waits for a person (ADR-004)
        }
        assert {e.status for e in inbox.edges} == {ReviewStatus.PROPOSED}
        assert {n.label for n in inbox.nodes} == {
            "Marco",
            "Ingestion",
            "Keep the flow synchronous",
            "Meeting 1",
        }
        assert all(e.evidence for e in inbox.edges) and not inbox.truncated
        ends = {n.id for n in inbox.nodes}
        assert all(e.source_id in ends and e.target_id in ends for e in inbox.edges)

    def test_a_decision_takes_a_proposal_out_of_the_inbox(self, store, ws) -> None:
        self._story(store, ws)
        first, second, third = store.pending_review(ws, OWNER, 10).edges
        store.review_relation(ws, OWNER, first.id, ReviewStatus.APPROVED)
        left = {e.id for e in store.pending_review(ws, OWNER, 10).edges}
        assert left == {second.id, third.id}
        store.review_relation(ws, OWNER, second.id, ReviewStatus.REJECTED)
        store.review_relation(ws, OWNER, third.id, ReviewStatus.APPROVED)
        empty = store.pending_review(ws, OWNER, 10)
        assert empty.edges == () and empty.nodes == ()

    def test_a_small_limit_says_there_is_more(self, store, ws) -> None:
        self._story(store, ws)
        some = store.pending_review(ws, OWNER, 2)
        assert len(some.edges) == 2 and some.truncated
        assert not store.pending_review(ws, OWNER, 3).truncated

    def test_nobody_else_sees_the_inbox(self, store, ws) -> None:
        self._story(store, ws)
        assert store.pending_review(ws, "mallory", 10).edges == ()
        assert store.pending_review(f"{ws}-other", OWNER, 10).edges == ()

    def test_erasing_the_meeting_empties_the_inbox(self, store, ws) -> None:
        self._story(store, ws)
        store.erase(ws, "m1")
        assert store.pending_review(ws, OWNER, 10).edges == ()
