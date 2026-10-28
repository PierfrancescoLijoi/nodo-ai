"""Finding things in the graph by meaning, across languages (ADR-024).

The graph names things in the language of the meeting; the question arrives in the language of the
person. Words alone do not bridge that. Each entity label gets a vector when it appears, a question
gets one when it is asked, and the nearest entities join the lexical matches as the starting points
of the walk over the graph."""

from __future__ import annotations

from types import SimpleNamespace

from nodo_api.adapters.memory import MemoryGraphStore
from nodo_api.services.entity_index import EntityIndexer
from tests.graph_contract import OWNER, meeting, queue_story

WS = "ws-entities"

# a tiny "model": meaning is the topic of the words, whatever the language
MEANING = {
    "marco": [1.0, 0.0, 0.0],
    "ingestion": [0.0, 1.0, 0.0],
    "inserimento": [0.0, 1.0, 0.0],  # Italian for the same thing
    "keep the flow synchronous": [0.0, 0.9, 0.3],
}


class Embedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    def embed(self, texts):
        if self.fail:
            raise ConnectionError("model unavailable")
        self.calls.append(list(texts))
        return [MEANING.get(t.lower(), [0.0, 0.0, 1.0]) for t in texts]


def graph_with_story() -> MemoryGraphStore:
    graph = MemoryGraphStore()
    first, _ = queue_story()
    graph.upsert_extraction(WS, OWNER, meeting(1), first)
    return graph


def test_every_new_entity_gets_a_vector_and_a_second_run_embeds_nothing() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    assert indexer.index_pending(WS, OWNER) == 3
    assert sorted(embedder.calls[0]) == ["Ingestion", "Keep the flow synchronous", "Marco"]
    assert indexer.index_pending(WS, OWNER) == 0 and len(embedder.calls) == 1


def test_a_question_in_another_language_finds_the_entity_by_meaning() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)
    near = indexer.nearest(WS, OWNER, "inserimento", 3)
    assert near[0].label == "Ingestion"


def test_a_question_that_cannot_be_embedded_finds_nothing_instead_of_failing() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)
    embedder.fail = True
    assert indexer.nearest(WS, OWNER, "inserimento", 3) == ()


def test_a_model_that_fails_leaves_the_entities_to_be_indexed_later() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    embedder.fail = True
    assert indexer.index_pending(WS, OWNER) == 0  # logged, not raised: analysis must not fail
    assert len(graph.entities_missing_vectors(WS, OWNER, 50, 3)) == 3
    embedder.fail = False
    assert indexer.index_pending(WS, OWNER) == 3


def test_work_is_done_in_bounded_batches() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3, batch=2, max_batches=1)
    assert indexer.index_pending(WS, OWNER) == 2
    assert indexer.index_pending(WS, OWNER) == 1


def test_a_vector_of_the_wrong_size_is_refused_before_it_is_stored() -> None:
    graph = graph_with_story()
    wrong = SimpleNamespace(embed=lambda texts: [[0.1, 0.2] for _ in texts])
    indexer = EntityIndexer(graph, wrong, dimensions=3)
    assert indexer.index_pending(WS, OWNER) == 0
    assert len(graph.entities_missing_vectors(WS, OWNER, 50, 3)) == 3


def test_a_question_in_another_language_starts_its_walk_at_the_entity_it_means() -> None:
    from nodo_api.services.query import QueryService

    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)

    def labels(entities) -> set[str]:
        return {n.label for n in entities.nodes}

    plain = QueryService(None, None, graph, None, None, 50)
    smart = QueryService(None, None, graph, None, None, 50, indexer)
    assert "Ingestion" not in labels(
        plain._context_graph(WS, OWNER, "inserimento")
    )  # no shared word
    found = labels(smart._context_graph(WS, OWNER, "inserimento"))
    assert "Ingestion" in found and "Keep the flow synchronous" in found  # and what hangs off it


def test_the_lexical_seeds_stay_when_the_model_is_down() -> None:
    from nodo_api.services.query import QueryService

    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)
    embedder.fail = True
    smart = QueryService(None, None, graph, None, None, 50, indexer)
    names = {n.label for n in smart._context_graph(WS, OWNER, "what about Marco").nodes}
    assert "Marco" in names


def test_a_model_with_a_query_side_is_asked_the_question_on_that_side() -> None:
    graph = graph_with_story()
    seen: list[str] = []

    class Split(Embedder):
        def embed_query(self, text):
            seen.append(text)
            return [MEANING.get(text.lower(), [0.0, 0.0, 1.0])]

    embedder = Split()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)
    assert indexer.nearest(WS, OWNER, "inserimento", 3)[0].label == "Ingestion"
    assert seen == ["inserimento"]


def test_a_vector_the_caller_already_has_is_not_computed_again() -> None:
    graph, embedder = graph_with_story(), Embedder()
    indexer = EntityIndexer(graph, embedder, dimensions=3)
    indexer.index_pending(WS, OWNER)
    calls = len(embedder.calls)
    near = indexer.nearest(WS, OWNER, "inserimento", 3, vector=[0.0, 1.0, 0.0])
    assert near[0].label == "Ingestion" and len(embedder.calls) == calls  # the model was not asked
    assert indexer.embed_question("inserimento") == [0.0, 1.0, 0.0]
    embedder.fail = True
    assert indexer.embed_question("inserimento") is None
