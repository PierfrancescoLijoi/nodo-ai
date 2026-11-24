"""Deterministic topology findings for the owner-scoped knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass

from nodo_api.ports import GraphStore


@dataclass(frozen=True, slots=True)
class GraphInsights:
    isolated: tuple[str, ...]
    bridges: tuple[str, ...]
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
        return GraphInsights(isolated, bridges, graph.truncated)
