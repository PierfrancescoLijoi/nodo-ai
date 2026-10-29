"""Finding entities in the graph by meaning, across languages (ADR-024).

An entity is named in the language of the meeting it came from, and a question arrives in the
language of the person asking. Words alone do not bridge that ("inserimento" against "ingestion"),
so every entity label gets a vector when it appears and a question gets one when it is asked; the
nearest entities join the lexical matches as the starting points of the walk over the graph.

Embedding is best effort. If the model is unavailable the analysis still completes, the entities
stay unindexed, and a later run (the next analysis of the owner, or index maintenance) catches up;
a question that can not be embedded simply has no semantic seeds, and the lexical ones remain."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from nodo_api.domain.graph import Entity
from nodo_api.ports import GraphStore

logger = logging.getLogger("nodo_entity_index")

BATCH = 64
MAX_BATCHES = 20  # 1 280 entities per call; more are picked up by the next run
MIN_SIMILARITY = 0.45  # cosine; calibrate per embedding model (ADR-024)


class EntityIndexer:
    def __init__(
        self,
        graph: GraphStore,
        embedder: Any,
        *,
        dimensions: int | None = None,
        batch: int = BATCH,
        max_batches: int = MAX_BATCHES,
        min_similarity: float = MIN_SIMILARITY,
    ) -> None:
        self._graph = graph
        self._embedder = embedder
        self._dimensions = dimensions
        self._batch = batch
        self._max_batches = max_batches
        self._min_similarity = min_similarity

    @property
    def embedder(self) -> Any:
        return self._embedder

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    def index_pending(self, workspace_id: str, owner_id: str) -> int:
        """Give a vector to every entity of the owner that has none (or one of another size).
        Returns how many were indexed; never raises for a model problem."""
        embed = getattr(self._embedder, "embed_passages", self._embedder.embed)
        done = 0
        for _ in range(self._max_batches):
            pending = self._graph.entities_missing_vectors(
                workspace_id, owner_id, self._batch, self._dimensions
            )
            if not pending:
                break
            try:
                vectors = embed([e.label for e in pending])
            except Exception as error:
                logger.error("entity_embedding_failed type=%s", type(error).__name__)
                break
            if self._dimensions is not None and any(len(v) != self._dimensions for v in vectors):
                logger.error("entity_embedding_wrong_size expected=%s", self._dimensions)
                break
            self._graph.put_entity_vectors(
                workspace_id,
                owner_id,
                {e.id: list(v) for e, v in zip(pending, vectors, strict=True)},
            )
            done += len(pending)
            if len(pending) < self._batch:
                break
        return done

    def embed_question(self, question: str) -> list[float] | None:
        """The question's vector, or None if the model can not give one."""
        try:
            ask = getattr(self._embedder, "embed_query", None)
            return list(ask(question)[0] if ask else self._embedder.embed([question])[0])
        except Exception as error:
            logger.error("question_embedding_failed type=%s", type(error).__name__)
            return None

    def nearest(
        self,
        workspace_id: str,
        owner_id: str,
        question: str,
        limit: int,
        vector: Sequence[float] | None = None,
    ) -> tuple[Entity, ...]:
        """The entities closest in meaning to the question; none if it can not be embedded. A
        vector the caller already has for this question saves a second call to the model."""
        vector = vector or self.embed_question(question)
        if not vector:
            return ()
        return self._graph.similar_entities(
            workspace_id, owner_id, vector, limit, self._min_similarity
        )
