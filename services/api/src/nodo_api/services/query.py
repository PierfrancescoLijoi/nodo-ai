from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from nodo_api.domain.answer_cache import CachedAnswer
from nodo_api.domain.graph import ORIGIN_TYPES, Subgraph
from nodo_api.domain.models import Answerability, Citation, Locale, TranscriptSegment
from nodo_api.domain.retrieval import CitationFormat, ResolvedQueryScope
from nodo_api.domain.sources import Fragment, FragmentHit
from nodo_api.errors import NotFoundError
from nodo_api.ports import (
    FragmentIndex,
    FragmentRepository,
    GraphStore,
    LlmClient,
    MeetingRepository,
    Retriever,
    SourceRepository,
    TranscriptRepository,
)
from nodo_api.resilience import DependencyUnavailable, Overloaded
from nodo_api.services.answer_cache import AnswerCache, Lookup
from nodo_api.services.context import expand_context
from nodo_api.services.entity_index import EntityIndexer
from nodo_api.services.section_summaries import SectionSummaryService
from nodo_api.telemetry import instruments, span, timed

MAX_GRAPH_DEPTH = 2
GRAPH_FANOUT = 15
MAX_EVIDENCE = 20
MAX_DEGRADED_SOURCES = 5
MAX_ENTITIES = 10
MAX_ENTITY_LENGTH = 64
SEED_ENTITIES = 5
SEMANTIC_SEEDS = 3  # a few: a near miss only adds a neighbourhood to the model's context
MAX_TERMS = 8
RRF_K = 60
MAX_CONTEXT_TOKENS = 2_000
_WORD = re.compile(r"\w+")
logger = logging.getLogger("nodo_query")


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
    scope: ResolvedQueryScope = field(default_factory=ResolvedQueryScope.legacy)
    citation_format: CitationFormat = CitationFormat.LEGACY


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    answer: str
    answerability: Answerability
    citations: tuple[Citation, ...]
    related_entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QueryResult(GeneratedAnswer):
    query_trace_id: str = ""
    degraded: bool = False  # the language model was unavailable: sources only, no written answer
    model_version: str = "mock-llm-v1"
    retrieval_version: str = "hybrid-source-v2"


def _citation_for_fragment(fragment: Fragment) -> Citation:
    """Build a citation without trusting generated fields during model degradation."""
    if fragment.locator.time_ms is not None:
        return Citation(
            fragment.source_id,
            fragment.id,
            fragment.locator.time_ms[0],
            fragment.locator.time_ms[1],
            fragment.text,
            fragment.speaker_id or "",
        )
    return Citation(
        quote=fragment.text,
        source_id=fragment.source_id,
        fragment_id=fragment.id,
        source_version=fragment.version,
        locator=fragment.locator,
    )


def _fuse_evidence(
    meetings: tuple[TranscriptSegment, ...], fragments: tuple[FragmentHit, ...]
) -> tuple[Fragment, ...]:
    """Reciprocal-rank fusion, with a deterministic source/ordinal tie-breaker."""
    ranked: dict[tuple[str, str], tuple[Fragment, float]] = {}
    for rank, segment in enumerate(meetings, start=1):
        fragment = Fragment.from_segment(segment, rank - 1)
        ranked[(fragment.source_id, fragment.id)] = (fragment, 1.0 / (RRF_K + rank))
    for rank, hit in enumerate(fragments, start=1):
        fragment = hit.fragment
        key = (fragment.source_id, fragment.id)
        score = 1.0 / (RRF_K + rank)
        earlier = ranked.get(key)
        ranked[key] = (fragment, score + (earlier[1] if earlier else 0.0))
    ordered = sorted(
        ranked.values(),
        key=lambda item: (-item[1], item[0].source_id, item[0].ordinal, item[0].id),
    )
    return tuple(fragment for fragment, _ in ordered[:MAX_EVIDENCE])


def _available(future: Any) -> tuple[Any, ...]:
    """A temporarily unavailable index may reduce recall, never relax the authorized filter."""
    try:
        return future.result()
    except (DependencyUnavailable, Overloaded):
        return ()


def entity_support(graph: Subgraph) -> dict[str, frozenset[tuple[str, str]]]:
    """For each entity label, the transcript segments that back the edges touching it."""
    nodes = {n.id: n for n in graph.nodes}
    support: dict[str, set[tuple[str, str]]] = {}
    for edge in graph.edges:
        evidence = {(e.source_id, e.fragment_id) for e in edge.evidence}
        for node_id in (edge.source_id, edge.target_id):
            node = nodes.get(node_id)
            if node is not None and node.type not in ORIGIN_TYPES:
                support.setdefault(node.label, set()).update(evidence)
    return {label: frozenset(keys) for label, keys in support.items()}


class EvidenceValidator:
    """Nothing the model says is trusted: citations and entities must be backed by evidence."""

    def __init__(
        self, transcripts: TranscriptRepository, fragments: FragmentRepository | None = None
    ) -> None:
        self._transcripts = transcripts
        self._fragments = fragments

    def validate(
        self,
        workspace_id: str,
        generated: GeneratedAnswer,
        retrieved: tuple[TranscriptSegment | Fragment, ...],
        graph: Subgraph | None = None,
        owns: Callable[[str], bool] | None = None,
    ) -> GeneratedAnswer:
        retrieved_keys = {self._key(item) for item in retrieved}
        valid = tuple(
            citation
            for citation in generated.citations
            if self._citation_key(citation) in retrieved_keys
            and (owns is None or owns(citation.evidence_source_id))
            and self._accessible(workspace_id, citation)
        )
        if not valid:
            return GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())
        answerability = generated.answerability
        if len(valid) < len(generated.citations):
            answerability = Answerability.PARTIAL
        cited = {self._citation_key(c) for c in valid}
        entities = self._entities(generated, entity_support(graph or Subgraph()), cited)
        return replace(
            generated, answerability=answerability, citations=valid, related_entities=entities
        )

    def still_valid(
        self, workspace_id: str, citations: tuple[Citation, ...], owns: Callable[[str], bool]
    ) -> bool:
        """An answer given earlier may be repeated only while every quote it cites is still there,
        word for word, in a meeting the person still owns."""
        return bool(citations) and all(
            owns(c.evidence_source_id) and self._accessible(workspace_id, c) for c in citations
        )

    def _accessible(self, workspace_id: str, citation: Citation) -> bool:
        if citation.source_id is not None or citation.fragment_id is not None:
            if self._fragments is None or not citation.source_id or not citation.fragment_id:
                return False
            fragment = self._fragments.get_fragment(
                workspace_id, citation.source_id, citation.fragment_id
            )
            return (
                fragment is not None
                and bool(citation.quote.strip())
                and citation.source_version is not None
                and citation.source_version > 0
                and fragment.version == citation.source_version
                and citation.locator is not None
                and fragment.locator == citation.locator
                and citation.quote in fragment.text
            )
        segment = self._transcripts.get_segment(
            workspace_id, citation.meeting_id, citation.segment_id
        )
        return segment is not None and (
            segment.start_ms == citation.start_ms
            and segment.end_ms == citation.end_ms
            and segment.text == citation.quote
        )

    @staticmethod
    def _key(item: TranscriptSegment | Fragment) -> tuple[str, str]:
        if isinstance(item, Fragment):
            return item.source_id, item.id
        return item.meeting_id, item.id

    @staticmethod
    def _citation_key(citation: Citation) -> tuple[str, str]:
        return citation.evidence_source_id, citation.evidence_fragment_id

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
        entities: EntityIndexer | None = None,
        answers: AnswerCache | None = None,
        sources: SourceRepository | None = None,
        fragment_index: FragmentIndex | None = None,
        fragments: FragmentRepository | None = None,
        section_summaries: SectionSummaryService | None = None,
    ) -> None:
        self._answers = answers
        self._meetings = meetings
        self._retriever = retriever
        self._graph = graph
        self._llm = llm
        self._validator = validator
        self._graph_node_limit = graph_node_limit
        self._entities = entities
        self._sources = sources
        self._fragment_index = fragment_index
        self._fragments = fragments
        self._section_summaries = section_summaries

    def _owner_check(self, workspace_id: str, user_id: str) -> Callable[[str], bool]:
        """Second look at every cited meeting (a handful), independent of the search filter."""
        cache: dict[str, bool] = {}

        def owns(source_id: str) -> bool:
            if source_id not in cache:
                try:
                    if self._sources is not None:
                        self._sources.get_owned(workspace_id, user_id, source_id)
                    else:
                        self._meetings.get_owned(workspace_id, user_id, source_id)
                    cache[source_id] = True
                except NotFoundError:
                    cache[source_id] = False
            return cache[source_id]

        return owns

    def query(self, workspace_id: str, user_id: str, request: QueryRequest) -> QueryResult:
        filters = request.filters.as_dict() if request.filters else None
        with timed("query_seconds", locale=request.ui_locale.value) as outcome, span("query"):
            lookup = self._lookup(workspace_id, user_id, request, filters)
            owns = self._owner_check(workspace_id, user_id)
            cached = self._from_cache(workspace_id, user_id, lookup, owns)
            outcome["cache"] = "hit" if cached else "miss" if lookup.ticket else "skip"
            instruments.answer_cache.add(1, {"outcome": outcome["cache"]})
            if cached is not None:
                outcome["degraded"] = False
                return cached
            result = self._answer(workspace_id, user_id, request, filters, lookup, owns)
            outcome["degraded"] = result.degraded
            if lookup.ticket is not None and self._answers is not None:
                self._answers.remember(
                    lookup.ticket,
                    CachedAnswer(
                        result.answer,
                        result.answerability,
                        result.citations,
                        result.related_entities,
                    ),
                    degraded=result.degraded,
                )
        return result

    def _lookup(
        self, workspace_id: str, user_id: str, request: QueryRequest, filters: dict[str, Any] | None
    ) -> Lookup:
        if self._answers is None:
            return Lookup()
        return self._answers.lookup(
            workspace_id,
            user_id,
            request.question,
            request.ui_locale,
            filters,
            {
                "citation_format": request.citation_format.value,
                "scope_mode": request.scope.mode.value,
                "source_ids": sorted(request.scope.source_ids)
                if request.scope.source_ids
                else None,
                "context_version": "section-v1",
                "retrieval_version": "hybrid-source-v2",
            },
        )

    def _from_cache(
        self, workspace_id: str, user_id: str, lookup: Lookup, owns: Callable[[str], bool]
    ) -> QueryResult | None:
        hit = lookup.hit
        if hit is None or self._answers is None:
            return None
        exact_negative = hit.answerability is Answerability.UNANSWERABLE
        if not exact_negative and not self._validator.still_valid(
            workspace_id, hit.citations, owns
        ):
            # Something the entry cites changed without the cache being told. Drop the owner's
            # entries rather than trust the rest, and answer this question the normal way.
            self._answers.invalidate(workspace_id, user_id)
            instruments.answer_cache.add(1, {"outcome": "stale"})
            return None
        return QueryResult(
            answer=hit.answer,
            answerability=hit.answerability,
            citations=hit.citations,
            related_entities=hit.related_entities,
            query_trace_id=str(uuid4()),
        )

    def _answer(
        self,
        workspace_id: str,
        user_id: str,
        request: QueryRequest,
        filters: dict[str, Any] | None,
        lookup: Lookup,
        owns: Callable[[str], bool],
    ) -> QueryResult:
        with span("query.retrieve") as retrieval:
            evidence = self._retrieve(workspace_id, user_id, request, filters)
            retrieval.set_attribute("nodo.evidence", len(evidence))
        with span("query.graph"):
            vector = lookup.ticket.vector if lookup.ticket is not None else None
            graph = self._context_graph(
                workspace_id, user_id, request.question, vector, request.scope
            )
        degraded = False
        try:
            with span("query.model"):
                generated = self._llm.answer(
                    workspace_id, request.question, request.ui_locale, evidence, graph
                )
        except (DependencyUnavailable, Overloaded):
            # Search worked and the model did not: the sentences found are still useful.
            degraded, generated = True, self._sources_only(evidence)
            instruments.query_degraded.add(1)
        with span("query.validate"):
            validated = self._validator.validate(workspace_id, generated, evidence, graph, owns)
        return QueryResult(
            answer=validated.answer,
            answerability=validated.answerability,
            citations=validated.citations,
            related_entities=validated.related_entities,
            query_trace_id=str(uuid4()),
            degraded=degraded,
        )

    @staticmethod
    def _sources_only(evidence: tuple[Fragment, ...]) -> GeneratedAnswer:
        citations = tuple(
            _citation_for_fragment(fragment) for fragment in evidence[:MAX_DEGRADED_SOURCES]
        )
        return GeneratedAnswer("", Answerability.PARTIAL, citations, ())

    def _retrieve(
        self,
        workspace_id: str,
        user_id: str,
        request: QueryRequest,
        filters: dict[str, Any] | None,
    ) -> tuple[Fragment, ...]:
        """Fuse authorized meeting and document searches with stable reciprocal-rank ordering.

        The legacy route remains meeting-only. Source scopes query both indexes concurrently, and
        every adapter receives the resolved source ids before it reads any candidate text.
        """
        scope = request.scope
        if scope.mode.value == "legacy":
            segments = self._retriever.search(
                workspace_id, user_id, request.question, filters, MAX_EVIDENCE
            )
            return tuple(
                Fragment.from_segment(segment, ordinal) for ordinal, segment in enumerate(segments)
            )

        source_ids = scope.source_ids
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="nodo-query") as pool:
            meetings = pool.submit(
                self._retriever.search,
                workspace_id,
                user_id,
                request.question,
                filters,
                MAX_EVIDENCE,
                source_ids=source_ids,
            )
            fragments = (
                pool.submit(
                    self._fragment_index.search,
                    workspace_id,
                    user_id,
                    request.question,
                    MAX_EVIDENCE,
                    source_ids=source_ids,
                )
                if self._fragment_index is not None
                else None
            )
            meeting_hits = _available(meetings)
            fragment_hits = _available(fragments) if fragments is not None else ()
        evidence = _fuse_evidence(meeting_hits, fragment_hits)
        return self._expand_context(workspace_id, evidence, request.ui_locale)

    def _expand_context(
        self, workspace_id: str, evidence: tuple[Fragment, ...], locale: Locale
    ) -> tuple[Fragment, ...]:
        if self._fragments is None:
            return evidence
        nearby: list[Fragment] = []
        for source_id in dict.fromkeys(fragment.source_id for fragment in evidence):
            try:
                nearby.extend(self._fragments.latest(workspace_id, source_id))
            except NotFoundError:
                continue
        summaries = []
        if self._section_summaries is not None:
            for section_id in dict.fromkeys(f.section_id for f in evidence if f.section_id):
                try:
                    summary = self._section_summaries.get_or_generate(
                        workspace_id, section_id, nearby, locale
                    )
                except Exception as error:
                    logger.warning("section_summary_skipped type=%s", type(error).__name__)
                else:
                    if summary is not None:
                        summaries.append(summary)
        return expand_context(evidence, nearby, summaries, MAX_CONTEXT_TOKENS).fragments

    def _context_graph(
        self,
        workspace_id: str,
        user_id: str,
        question: str,
        vector: Sequence[float] | None = None,
        scope: ResolvedQueryScope | None = None,
    ) -> Subgraph:
        """Entities the question names, plus their reviewed neighbourhood, within hard bounds."""
        terms = tuple(dict.fromkeys(w for w in _WORD.findall(question.lower()) if len(w) >= 3))[
            :MAX_TERMS
        ]
        legacy = scope is None or scope.mode.value == "legacy"
        source_ids = None if legacy else scope.source_ids
        graph_scope = {"meetings_only": legacy}
        if not legacy:
            graph_scope["source_ids"] = source_ids
        seeds = list(
            self._graph.find_entities(
                workspace_id,
                user_id,
                terms,
                SEED_ENTITIES,
                **graph_scope,
            )
        )
        if self._entities is not None:
            # Entities the question means in other words or another language join the ones it names.
            known = {e.id for e in seeds}
            seeds += [
                e
                for e in self._entities.nearest(
                    workspace_id, user_id, question, SEMANTIC_SEEDS, vector
                )
                if e.id not in known
            ]
        return self._graph.neighborhood(
            workspace_id,
            user_id,
            [e.id for e in seeds],
            depth=MAX_GRAPH_DEPTH,
            node_limit=self._graph_node_limit,
            fanout=GRAPH_FANOUT,
            include_proposed=False,
            as_of=None,
            **graph_scope,
        )
