"""Deterministic topology findings for the owner-scoped knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass

from nodo_api.domain.graph import EntityType, RelationKind, ReviewStatus
from nodo_api.ports import GraphStore


@dataclass(frozen=True, slots=True)
class GraphInsights:
    isolated: tuple[str, ...]
    bridges: tuple[str, ...]
    orphan_concepts: tuple[str, ...]
    duplicate_concepts: tuple[str, ...]
    contradicting_decisions: tuple[str, ...]
    truncated: bool


class GraphInsightService:
    def __init__(self, graph: GraphStore) -> None:
        self._graph = graph

    def analyze(self, workspace_id: str, owner_id: str, limit: int = 200) -> GraphInsights:
        graph = self._graph.analysis_graph(workspace_id, owner_id, limit)
        neighbors = {node.id: set() for node in graph.nodes}
        for edge in graph.edges:
            neighbors.setdefault(edge.source_id, set()).add(edge.target_id)
            neighbors.setdefault(edge.target_id, set()).add(edge.source_id)
        isolated = tuple(sorted(node_id for node_id, adjacent in neighbors.items() if not adjacent))
        bridges = tuple(
            sorted(node_id for node_id, adjacent in neighbors.items() if len(adjacent) >= 3)
        )
        concepts = tuple(
            node
            for node in graph.nodes
            if node.type in {EntityType.TOPIC, EntityType.PROJECT, EntityType.SYSTEM}
        )
        orphan_concepts = tuple(sorted(node.id for node in concepts if not neighbors.get(node.id)))
        labels: dict[str, list[str]] = {}
        for node in concepts:
            labels.setdefault(node.label.casefold(), []).append(node.id)
        duplicate_concepts = tuple(
            sorted(node_id for values in labels.values() if len(values) > 1 for node_id in values)
        )
        contradicting_decisions = tuple(
            sorted(
                edge.id
                for edge in graph.edges
                if edge.kind is RelationKind.CONTRADICTS
                and edge.status is ReviewStatus.APPROVED
            )
        )
        return GraphInsights(
            isolated,
            bridges,
            orphan_concepts,
            duplicate_concepts,
            contradicting_decisions,
            graph.truncated,
        )
