import type { Graph, GraphNode } from '@/data/types';

interface LayoutOptions {
  width: number;
  height: number;
  maxNodes: number;
}

export interface PositionedNode extends GraphNode {
  x: number;
  y: number;
}

export interface LayoutResult {
  nodes: PositionedNode[];
  edges: Graph['edges'];
  leftOut: GraphNode[];
}

const scoreEdge = (edge: Graph['edges'][number]) => (edge.status === 'approved' ? 2 : 0) + (edge.confidence ?? 0);

export function radialLayout(graph: Graph, centerId: string, options: LayoutOptions): LayoutResult {
  const center = graph.nodes.find((node) => node.id === centerId) ?? graph.nodes[0];
  if (!center) return { nodes: [], edges: [], leftOut: [] };
  const connected = graph.edges
    .filter((edge) => edge.from === center.id || edge.to === center.id)
    .sort(
      (a, b) =>
        scoreEdge(b) - scoreEdge(a) || `${a.from}:${a.kind}:${a.to}`.localeCompare(`${b.from}:${b.kind}:${b.to}`),
    );
  const neighbourIds = [...new Set(connected.map((edge) => (edge.from === center.id ? edge.to : edge.from)))];
  const ordered = neighbourIds.flatMap((id) => graph.nodes.filter((node) => node.id === id));
  const visible = ordered.slice(0, Math.max(0, options.maxNodes - 1));
  const radius = Math.min(options.width / 2 - 36, options.height / 2 - 36);
  const positioned: PositionedNode[] = [
    { ...center, x: options.width / 2, y: options.height / 2 },
    ...visible.map((node, index) => {
      const angle = -Math.PI / 2 + (index * Math.PI * 2) / visible.length;
      return {
        ...node,
        x: Math.round(options.width / 2 + Math.cos(angle) * radius),
        y: Math.round(options.height / 2 + Math.sin(angle) * radius),
      };
    }),
  ];
  const ids = new Set(positioned.map((node) => node.id));
  return {
    nodes: positioned,
    edges: graph.edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to)),
    leftOut: graph.nodes.filter((node) => !ids.has(node.id)),
  };
}
