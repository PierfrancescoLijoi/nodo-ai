"""Concept hierarchy reads and review for the API (ADR-030). Local and memory engines only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from nodo_api.domain.graph import Entity, EntityType, Evidence, Relation, ReviewStatus, Subgraph
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Locator
from nodo_api.errors import ValidationFailed
from nodo_api.ports import FragmentRepository, HierarchyStore, ParentSuggester, SourceRepository
from nodo_api.services.graph import GraphService

MENTIONS = 20
logger = logging.getLogger("nodo_hierarchy")


@dataclass(frozen=True, slots=True)
class ConceptHierarchy:
    concept: Entity
    mentioned_in: tuple[tuple[str, str | None], ...]  # (source id, current title)
    graph: Subgraph


class HierarchyService:
    def __init__(
        self,
        store: HierarchyStore,
        graph: GraphService,
        fragments: FragmentRepository,
        sources: SourceRepository,
        suggester: ParentSuggester | None = None,
    ) -> None:
        self._store = store
        self._graph = graph
        self._fragments = fragments
        self._sources = sources
        self._suggester = suggester

    @property
    def can_suggest(self) -> bool:
        return self._suggester is not None

    def concept(
        self,
        workspace_id: str,
        user_id: str,
        entity_id: str,
        *,
        direction: Literal["parents", "children", "both"],
        depth: int,
        include_proposed: bool,
        limit: int,
    ) -> ConceptHierarchy:
        graph = self._store.hierarchy(
            workspace_id,
            user_id,
            entity_id,
            direction=direction,
            depth=depth,
            include_proposed=include_proposed,
            limit=limit,
        )
        origins = self._store.mentions(workspace_id, user_id, entity_id, MENTIONS)
        ids = [o.meeting_id or o.source_id for o in origins]
        ids = [i for i in ids if i is not None]
        # Titles are read now, from the sources table, so a renamed source or meeting shows its
        # current title; a source that is gone is left out.
        titles = self._sources.titles(workspace_id, user_id, ids)
        mentioned = tuple((i, titles[i]) for i in ids if i in titles)
        return ConceptHierarchy(graph.nodes[0], mentioned, graph)

    def proposals(self, workspace_id: str, user_id: str, limit: int) -> Subgraph:
        return self._store.pending_hierarchy(workspace_id, user_id, limit)

    def review(
        self, workspace_id: str, user_id: str, relation_id: str, status: ReviewStatus
    ) -> Relation:
        self._store.child_of(workspace_id, user_id, relation_id)  # 404 for any other relation
        return self._graph.review(workspace_id, user_id, relation_id, status, meetings_only=False)

    def locate(self, workspace_id: str, evidence: Evidence) -> Locator | None:
        fragment = self._fragments.get_fragment(
            workspace_id, evidence.source_id, evidence.fragment_id
        )
        return fragment.locator if fragment is not None else None

    def suggest_parents(
        self, workspace_id: str, user_id: str, concept_id: str, locale: Locale
    ) -> tuple[Relation, ...]:
        graph = self._store.hierarchy(
            workspace_id,
            user_id,
            concept_id,
            direction="parents",
            depth=1,
            include_proposed=True,
            limit=2,
        )
        concept = graph.nodes[0]
        if concept.type not in {EntityType.PROJECT, EntityType.SYSTEM, EntityType.TOPIC}:
            raise ValidationFailed
        if self._suggester is None:
            return ()
        co_mentions = self._store.co_mentions(workspace_id, user_id, concept_id, 8)
        if not co_mentions:
            logger.info("parent_suggestions candidates=0 proposed=0")
            return ()
        fragments = []
        seen: set[tuple[str, str]] = set()
        for candidate in co_mentions:
            for evidence in candidate.evidence[:2]:
                key = (evidence.source_id, evidence.fragment_id)
                if key in seen:
                    continue
                found = self._fragments.get_fragment(workspace_id, *key)
                if found is not None:
                    fragments.append(found)
                    seen.add(key)
                if len(fragments) == 6:
                    break
            if len(fragments) == 6:
                break
        candidates = tuple(item.entity for item in co_mentions)
        suggestions = self._suggester.suggest(concept, candidates, tuple(fragments), locale)
        allowed = {item.entity.id: frozenset(item.evidence) for item in co_mentions}
        proposed = []
        for suggestion in suggestions:
            evidence = tuple(
                Evidence(fragment.source_id, fragment.id)
                for fragment in fragments
                if fragment.id in suggestion.evidence
                and Evidence(fragment.source_id, fragment.id)
                in allowed.get(suggestion.candidate_id, frozenset())
            )
            relation = self._store.propose_child_of(
                workspace_id,
                user_id,
                concept_id,
                suggestion.candidate_id,
                suggestion.confidence,
                evidence,
            )
            if relation is not None and relation.status is not ReviewStatus.REJECTED:
                proposed.append(relation)
        logger.info("parent_suggestions candidates=%d proposed=%d", len(candidates), len(proposed))
        return tuple(proposed)
