from datetime import UTC, datetime

from nodo_api.domain.graph import Entity, EntityType, Relation, RelationKind, ReviewStatus, Subgraph
from nodo_api.services.graph_insights import GraphInsightService


class _Graph:
    def analysis_graph(self, workspace_id, owner_id, limit):
        nodes = tuple(
            Entity(node_id, EntityType.TOPIC, node_id, None, datetime(2026, 11, 24, tzinfo=UTC))
            for node_id in ("a", "b", "c", "d", "alone")
        )
        pairs = (("ab", "b"), ("ac", "c"), ("ad", "d"))
        edges = tuple(_edge(edge_id, "a", target) for edge_id, target in pairs)
        return Subgraph(nodes, edges, False)


def _edge(edge_id, source, target):
    return Relation(
        edge_id, source, target, RelationKind.DEPENDS_ON, ReviewStatus.APPROVED, None, None, 1, ()
    )


def test_graph_insights_find_isolated_and_bridge_nodes():
    result = GraphInsightService(_Graph()).analyze("ws", "owner")

    assert result.isolated == ("alone",)
    assert result.bridges == ("a",)
    assert result.orphan_concepts == ("alone",)
    assert result.duplicate_concepts == ()
    assert result.contradicting_decisions == ()
    assert not result.truncated
