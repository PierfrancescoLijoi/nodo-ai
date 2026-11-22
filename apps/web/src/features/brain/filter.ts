import type { EntityType, Graph, RelationKind } from '@/lib/domain/types';

export type ReviewStatus = 'approved' | 'proposed';

export interface BrainFilters {
  query: string;
  types: ReadonlySet<EntityType>;
  kinds: ReadonlySet<RelationKind>;
  statuses: ReadonlySet<ReviewStatus>;
}

export const EMPTY_FILTERS: BrainFilters = {
  query: '',
  types: new Set(),
  kinds: new Set(),
  statuses: new Set(),
};

export function filterGraph(graph: Graph, filters: BrainFilters): Graph {
  const query = filters.query.trim().toLocaleLowerCase();
  const hasNodeFilter = query.length > 0 || filters.types.size > 0;
  const hasEdgeFilter = filters.kinds.size > 0 || filters.statuses.size > 0;
  const candidates = new Set(
    graph.nodes
      .filter(
        (node) =>
          (!query || node.label.toLocaleLowerCase().includes(query)) &&
          (filters.types.size === 0 || filters.types.has(node.type)),
      )
      .map((node) => node.id),
  );
  const edges = graph.edges.filter(
    (edge) =>
      (filters.kinds.size === 0 || filters.kinds.has(edge.kind)) &&
      (filters.statuses.size === 0 || (edge.status !== undefined && filters.statuses.has(edge.status))) &&
      (!hasNodeFilter || candidates.has(edge.from) || candidates.has(edge.to)),
  );
  const visible = new Set(hasNodeFilter ? candidates : []);
  for (const edge of edges) {
    visible.add(edge.from);
    visible.add(edge.to);
  }
  if (!hasNodeFilter && !hasEdgeFilter) for (const node of graph.nodes) visible.add(node.id);
  return {
    nodes: graph.nodes.filter((node) => visible.has(node.id)),
    edges: edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to)),
    truncated: graph.truncated,
  };
}

export function mergeGraphs(current: Graph, expansion: Graph): Graph {
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  for (const node of expansion.nodes) nodes.set(node.id, node);
  for (const edge of expansion.edges) edges.set(edge.id, edge);
  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    truncated: current.truncated || expansion.truncated,
  };
}
