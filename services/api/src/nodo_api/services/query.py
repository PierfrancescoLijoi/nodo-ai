from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.models import Answerability, Citation, Locale, TranscriptSegment
from nodo_api.ports import GraphStore, LlmClient, Retriever, TranscriptRepository


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
    def __init__(self, transcripts: TranscriptRepository) -> None:
        self._transcripts = transcripts

    def validate(
        self,
        workspace_id: str,
        generated: GeneratedAnswer,
        retrieved: tuple[TranscriptSegment, ...],
    ) -> GeneratedAnswer:
        retrieved_keys = {(segment.meeting_id, segment.id) for segment in retrieved}
        valid = tuple(
            citation
            for citation in generated.citations
            if (citation.meeting_id, citation.segment_id) in retrieved_keys
            and self._accessible(workspace_id, citation)
        )
        if not valid:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        answerability = generated.answerability
        if len(valid) < len(generated.citations):
            answerability = Answerability.PARTIAL
        return replace(generated, answerability=answerability, citations=valid)

    def _accessible(self, workspace_id: str, citation: Citation) -> bool:
        segment = self._transcripts.get_segment(workspace_id, citation.segment_id)
        return segment is not None and (
            segment.meeting_id == citation.meeting_id
            and segment.start_ms == citation.start_ms
            and segment.end_ms == citation.end_ms
            and segment.text == citation.quote
        )


class QueryService:
    def __init__(
        self,
        retriever: Retriever,
        graph: GraphStore,
        llm: LlmClient,
        validator: EvidenceValidator,
        graph_node_limit: int,
    ) -> None:
        self._retriever = retriever
        self._graph = graph
        self._llm = llm
        self._validator = validator
        self._graph_node_limit = graph_node_limit

    def query(self, workspace_id: str, request: QueryRequest) -> QueryResult:
        filters = request.filters.as_dict() if request.filters else None
        evidence = self._retriever.search(workspace_id, request.question, filters)
        entity = "ingestion" if "ingestion" in request.question.lower() else request.question
        graph = self._graph.lookup(workspace_id, entity, 2, self._graph_node_limit)
        generated = self._llm.answer(
            workspace_id, request.question, request.ui_locale, evidence, graph
        )
        validated = self._validator.validate(workspace_id, generated, evidence)
        return QueryResult(
            answer=validated.answer,
            answerability=validated.answerability,
            citations=validated.citations,
            related_entities=validated.related_entities,
            query_trace_id=str(uuid4()),
        )
