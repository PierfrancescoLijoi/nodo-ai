"""Concept hierarchy reads and review for the API (ADR-030). Local and memory engines only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nodo_api.domain.graph import Entity, Evidence, Relation, ReviewStatus, Subgraph
from nodo_api.domain.sources import Locator
from nodo_api.ports import FragmentRepository, HierarchyStore, SourceRepository
from nodo_api.services.graph import GraphService

MENTIONS = 20


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
    ) -> None:
        self._store = store
        self._graph = graph
        self._fragments = fragments
        self._sources = sources

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
