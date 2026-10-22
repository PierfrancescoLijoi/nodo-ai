from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.graph import EntityType, Subgraph
from nodo_api.domain.models import Answerability, Citation, Locale, TranscriptSegment
from nodo_api.errors import NotFoundError
from nodo_api.ports import GraphStore, LlmClient, MeetingRepository, Retriever, TranscriptRepository

MAX_GRAPH_DEPTH = 2
GRAPH_FANOUT = 15
MAX_EVIDENCE = 20
MAX_ENTITIES = 10
MAX_ENTITY_LENGTH = 64
SEED_ENTITIES = 5
MAX_TERMS = 8
_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class QueryFilters:
    time_from: datetime | None = None
    time_to: datetime | None = None
    project: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "time_from": self.time_from,
                "time_to": self.time_to,
                "project": self.project,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class QueryRequest:
    question: str
    ui_locale: Locale
    filters: QueryFilters | None = None


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    answer: str
    answerability: Answerability
    citations: tuple[Citation, ...]
    related_entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QueryResult(GeneratedAnswer):
    query_trace_id: str = ""
    model_version: str = "mock-llm-v1"
    retrieval_version: str = "hybrid-graph-v1"


def entity_support(graph: Subgraph) -> dict[str, frozenset[tuple[str, str]]]:
    """For each entity label, the transcript segments that back the edges touching it."""
    nodes = {n.id: n for n in graph.nodes}
    support: dict[str, set[tuple[str, str]]] = {}
    for edge in graph.edges:
        evidence = {(e.meeting_id, e.segment_id) for e in edge.evidence}
        for node_id in (edge.source_id, edge.target_id):
            node = nodes.get(node_id)
            if node is not None and node.type is not EntityType.MEETING:
                support.setdefault(node.label, set()).update(evidence)
    return {label: frozenset(keys) for label, keys in support.items()}


class EvidenceValidator:
    """Nothing the model says is trusted: citations and entities must be backed by evidence."""

    def __init__(self, transcripts: TranscriptRepository) -> None:
        self._transcripts = transcripts

    def validate(
        self,
        workspace_id: str,
        generated: GeneratedAnswer,
        retrieved: tuple[TranscriptSegment, ...],
        graph: Subgraph | None = None,
        owns: Callable[[str], bool] | None = None,
    ) -> GeneratedAnswer:
        retrieved_keys = {(segment.meeting_id, segment.id) for segment in retrieved}
        valid = tuple(
            citation
            for citation in generated.citations
            if (citation.meeting_id, citation.segment_id) in retrieved_keys
            and (owns is None or owns(citation.meeting_id))
            and self._accessible(workspace_id, citation)
        )
        if not valid:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        answerability = generated.answerability
        if len(valid) < len(generated.citations):
            answerability = Answerability.PARTIAL
        cited = {(c.meeting_id, c.segment_id) for c in valid}
        entities = self._entities(generated, entity_support(graph or Subgraph()), cited)
        return replace(
            generated, answerability=answerability, citations=valid, related_entities=entities
        )

    def _accessible(self, workspace_id: str, citation: Citation) -> bool:
        segment = self._transcripts.get_segment(
            workspace_id, citation.meeting_id, citation.segment_id
        )
        return segment is not None and (
            segment.start_ms == citation.start_ms
            and segment.end_ms == citation.end_ms
            and segment.text == citation.quote
        )

    @staticmethod
    def _entities(
        generated: GeneratedAnswer,
        support: dict[str, frozenset[tuple[str, str]]],
        cited: set[tuple[str, str]],
    ) -> tuple[str, ...]:
        claimed = (e for e in generated.related_entities if len(e) <= MAX_ENTITY_LENGTH)
        backed = (e for e in claimed if support.get(e, frozenset()) & cited)
        return tuple(dict.fromkeys(backed))[:MAX_ENTITIES]


class QueryService:
    def __init__(
        self,
        meetings: MeetingRepository,
        retriever: Retriever,
        graph: GraphStore,
        llm: LlmClient,
        validator: EvidenceValidator,
        graph_node_limit: int,
    ) -> None:
        self._meetings = meetings
        self._retriever = retriever
        self._graph = graph
        self._llm = llm
        self._validator = validator
        self._graph_node_limit = graph_node_limit

    def _owner_check(self, workspace_id: str, user_id: str) -> Callable[[str], bool]:
        """Second look at every cited meeting (a handful), independent of the search filter."""
        cache: dict[str, bool] = {}

        def owns(meeting_id: str) -> bool:
            if meeting_id not in cache:
                try:
                    self._meetings.get_owned(workspace_id, user_id, meeting_id)
                    cache[meeting_id] = True
                except NotFoundError:
                    cache[meeting_id] = False
            return cache[meeting_id]

        return owns

    def query(self, workspace_id: str, user_id: str, request: QueryRequest) -> QueryResult:
        filters = request.filters.as_dict() if request.filters else None
        evidence = self._retriever.search(
            workspace_id, user_id, request.question, filters, MAX_EVIDENCE
        )[:MAX_EVIDENCE]
        graph = self._context_graph(workspace_id, user_id, request.question)
        generated = self._llm.answer(
            workspace_id, request.question, request.ui_locale, evidence, graph
        )
        validated = self._validator.validate(
            workspace_id, generated, evidence, graph, self._owner_check(workspace_id, user_id)
        )
        return QueryResult(
            answer=validated.answer,
            answerability=validated.answerability,
            citations=validated.citations,
            related_entities=validated.related_entities,
            query_trace_id=str(uuid4()),
        )

    def _context_graph(self, workspace_id: str, user_id: str, question: str) -> Subgraph:
        """Entities the question names, plus their reviewed neighbourhood, within hard bounds."""
        terms = tuple(dict.fromkeys(w for w in _WORD.findall(question.lower()) if len(w) >= 3))[
            :MAX_TERMS
        ]
        seeds = self._graph.find_entities(workspace_id, user_id, terms, SEED_ENTITIES)
        return self._graph.neighborhood(
            workspace_id,
            user_id,
            [e.id for e in seeds],
            depth=MAX_GRAPH_DEPTH,
            node_limit=self._graph_node_limit,
            fanout=GRAPH_FANOUT,
            include_proposed=False,
            as_of=None,
        )
