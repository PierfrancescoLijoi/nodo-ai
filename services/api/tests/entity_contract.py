"""Finding entities by meaning: what every GraphStore must do (ADR-024). Mixed into the contract
classes of the memory, SQLite and PostgreSQL stores so the three can not drift apart."""

from __future__ import annotations

from tests.graph_contract import OWNER, meeting, queue_story

VECTORS = {
    "Marco": [1.0, 0.0, 0.0],
    "Ingestion": [0.0, 1.0, 0.0],
    "Keep the flow synchronous": [0.0, 0.9, 0.3],
}


class EntityVectorContract:
    """Subclasses provide the `store` and `ws` fixtures."""

    def _story(self, store, ws):
        first, _ = queue_story()
        store.upsert_extraction(ws, OWNER, meeting(1), first)
        return store.entities_missing_vectors(ws, OWNER, 50, None)

    def test_entities_without_a_vector_are_listed_and_the_nearest_come_back_by_meaning(
        self, store, ws
    ) -> None:
        missing = self._story(store, ws)
        assert {e.label for e in missing} == set(VECTORS)  # never the meeting node
        store.put_entity_vectors(ws, OWNER, {e.id: VECTORS[e.label] for e in missing})
        assert store.entities_missing_vectors(ws, OWNER, 50, 3) == ()

        near = store.similar_entities(ws, OWNER, [0.0, 1.0, 0.1], 5, 0.5)
        assert [e.label for e in near] == ["Ingestion", "Keep the flow synchronous"]
        far = store.similar_entities(ws, OWNER, [1.0, 0.1, 0.0], 5, 0.5)
        assert [e.label for e in far] == ["Marco"]
        assert len(store.similar_entities(ws, OWNER, [0.0, 1.0, 0.1], 1, 0.0)) == 1
        assert store.similar_entities(ws, OWNER, [0.0, 0.0, 1.0], 5, 0.9) == ()

    def test_vectors_belong_to_their_owner_and_workspace(self, store, ws) -> None:
        entities = self._story(store, ws)
        store.put_entity_vectors(ws, OWNER, {e.id: [1.0, 0.0, 0.0] for e in entities})
        assert store.similar_entities(ws, "mallory", [1.0, 0.0, 0.0], 5, 0.0) == ()
        assert store.similar_entities(f"{ws}-other", OWNER, [1.0, 0.0, 0.0], 5, 0.0) == ()
        # a vector put under another owner for an entity that is not theirs attaches to nothing
        store.put_entity_vectors(ws, "mallory", {entities[0].id: [1.0, 0.0, 0.0]})
        assert store.similar_entities(ws, "mallory", [1.0, 0.0, 0.0], 5, 0.0) == ()

    def test_a_vector_of_another_size_is_ignored_and_the_entity_is_listed_again(
        self, store, ws
    ) -> None:
        entities = self._story(store, ws)
        store.put_entity_vectors(ws, OWNER, {e.id: [1.0, 0.0] for e in entities})  # an old model
        assert store.similar_entities(ws, OWNER, [1.0, 0.0, 0.0], 5, 0.0) == ()
        assert len(store.entities_missing_vectors(ws, OWNER, 50, 3)) == len(entities)
        assert store.entities_missing_vectors(ws, OWNER, 50, 2) == ()
        store.put_entity_vectors(ws, OWNER, {e.id: [1.0, 0.0, 0.0] for e in entities})
        assert store.entities_missing_vectors(ws, OWNER, 50, 3) == ()  # replaced

    def test_vectors_go_with_the_entities_when_a_meeting_is_erased(self, store, ws) -> None:
        entities = self._story(store, ws)
        store.put_entity_vectors(ws, OWNER, {e.id: [1.0, 0.0, 0.0] for e in entities})
        store.erase(ws, "m1")
        assert store.similar_entities(ws, OWNER, [1.0, 0.0, 0.0], 5, 0.0) == ()
