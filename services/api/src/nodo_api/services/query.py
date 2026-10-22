from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.models import Answerability, Citation, GraphNode, Locale, TranscriptSegment
from nodo_api.ports import GraphStore, LlmClient, MeetingRepository, Retriever, TranscriptRepository

MAX_GRAPH_DEPTH = 2
MAX_EVIDENCE = 20
MAX_ENTITIES = 10
MAX_ENTITY_LENGTH = 64
MAX_ENTITY_QUERY = 128


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


class EvidenceValidator:
    """Nothing the model says is trusted: citations and entities must be backed by evidence."""

    def __init__(self, transcripts: TranscriptRepository) -> None:
        self._transcripts = transcripts

    def validate(
        self,
        workspace_id: str,
        generated: GeneratedAnswer,
        retrieved: tuple[TranscriptSegment, ...],
        graph: tuple[GraphNode, ...] = (),
        allowed_meeting_ids: frozenset[str] | None = None,
    ) -> GeneratedAnswer:
        retrieved_keys = {(segment.meeting_id, segment.id) for segment in retrieved}
        valid = tuple(
            citation
            for citation in generated.citations
            if (citation.meeting_id, citation.segment_id) in retrieved_keys
            and (allowed_meeting_ids is None or citation.meeting_id in allowed_meeting_ids)
            and self._accessible(workspace_id, citation)
        )
        if not valid:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        answerability = generated.answerability
        if len(valid) < len(generated.citations):
            answerability = Answerability.PARTIAL
        entities = self._entities(generated, graph, {(c.meeting_id, c.segment_id) for c in valid})
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
        graph: tuple[GraphNode, ...],
        cited: set[tuple[str, str]],
    ) -> tuple[str, ...]:
        backed = {node.label for node in graph if (node.meeting_id, node.segment_id) in cited}
        claimed = (e for e in generated.related_entities if len(e) <= MAX_ENTITY_LENGTH)
        return tuple(dict.fromkeys(e for e in claimed if e in backed))[:MAX_ENTITIES]


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

    def query(self, workspace_id: str, user_id: str, request: QueryRequest) -> QueryResult:
        allowed = self._meetings.owned_ids(workspace_id, user_id)
        filters = request.filters.as_dict() if request.filters else None
        evidence = self._retriever.search(
            workspace_id, request.question, filters, allowed, MAX_EVIDENCE
        )[:MAX_EVIDENCE]
        entity = "ingestion" if "ingestion" in request.question.lower() else request.question
        graph = self._graph.lookup(
            workspace_id,
            entity[:MAX_ENTITY_QUERY],
            MAX_GRAPH_DEPTH,
            self._graph_node_limit,
            allowed,
        )[: self._graph_node_limit]
        generated = self._llm.answer(
            workspace_id, request.question, request.ui_locale, evidence, graph
        )
        validated = self._validator.validate(workspace_id, generated, evidence, graph, allowed)
        return QueryResult(
            answer=validated.answer,
            answerability=validated.answerability,
            citations=validated.citations,
            related_entities=validated.related_entities,
            query_trace_id=str(uuid4()),
        )
