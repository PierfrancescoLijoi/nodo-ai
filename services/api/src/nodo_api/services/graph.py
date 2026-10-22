"""Graph reads and review for the API. Scope rule: a user sees and changes only their own graph."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from nodo_api.domain.graph import Change, MeetingDigest, Relation, ReviewStatus, Subgraph
from nodo_api.ports import GraphStore
from nodo_api.services.query import GRAPH_FANOUT

OVERVIEW_SEEDS = 8
ENTITY_SEEDS = 5
MAX_NODES = 200
_WORD = re.compile(r"\w+")


class GraphService:
    def __init__(self, graph: GraphStore) -> None:
        self._graph = graph

    def subgraph(
        self,
        workspace_id: str,
        user_id: str,
        *,
        seed_id: str | None,
        entity: str | None,
        depth: int,
        include_proposed: bool,
        as_of: datetime | None,
        limit: int,
    ) -> Subgraph:
        if seed_id:
            seeds = [seed_id]
        elif entity:
            found = self._graph.find_entities(
                workspace_id, user_id, _WORD.findall(entity.lower()), ENTITY_SEEDS
            )
            seeds = [e.id for e in found]
        else:
            seeds = [e.id for e in self._graph.overview(workspace_id, user_id, OVERVIEW_SEEDS)]
        return self._graph.neighborhood(
            workspace_id,
            user_id,
            seeds,
            depth=min(depth, 2),
            node_limit=min(limit, MAX_NODES),
            fanout=GRAPH_FANOUT,
            include_proposed=include_proposed,
            as_of=as_of,
        )

    def review(
        self, workspace_id: str, user_id: str, relation_id: str, status: ReviewStatus
    ) -> Relation:
        return self._graph.review_relation(workspace_id, user_id, relation_id, status)

    def changes(self, workspace_id: str, user_id: str, limit: int) -> tuple[Change, ...]:
        return self._graph.recent_changes(workspace_id, user_id, limit)

    def digests(
        self, workspace_id: str, user_id: str, meeting_ids: Sequence[str]
    ) -> dict[str, MeetingDigest]:
        return self._graph.meeting_digests(workspace_id, user_id, meeting_ids)
