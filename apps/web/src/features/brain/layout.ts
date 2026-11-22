import type { EntityType, Graph, GraphNode } from '@/lib/domain/types';

export interface BrainLayoutOptions {
  width: number;
  height: number;
}

export interface BrainNode extends GraphNode {
  x: number;
  y: number;
  z: number;
  radius: number;
  regionId: EntityType;
}

export interface BrainRegion {
  id: EntityType;
  x: number;
  y: number;
  count: number;
}

export interface BrainLayout {
  width: number;
  height: number;
  nodes: BrainNode[];
  edges: Graph['edges'];
  regions: BrainRegion[];
}

const REGION: Record<EntityType, readonly [number, number]> = {
  decision: [-0.58, -0.16],
  action: [-0.48, 0.18],
  person: [-0.2, 0.34],
  project: [-0.05, -0.42],
  meeting: [0.08, 0.03],
  topic: [0.48, -0.18],
  system: [0.55, 0.42],
};

const TYPE_ORDER = Object.keys(REGION) as EntityType[];
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function hash(value: string): number {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return result >>> 0;
}

/**
 * A stable regional layout. `regionId` is deliberately independent of a backend hierarchy so a future
 * CHILD_OF relation can replace type regions without changing rendering or interaction contracts.
 */
export function brainLayout(graph: Graph, options: BrainLayoutOptions): BrainLayout {
  const centerX = options.width / 2;
  const centerY = options.height / 2;
  const brainX = options.width * 0.425;
  const brainY = options.height * 0.415;
  const grouped = new Map(TYPE_ORDER.map((type) => [type, [] as GraphNode[]]));
  for (const node of graph.nodes) grouped.get(node.type)?.push(node);
  const degrees = new Map<string, number>();
  for (const edge of graph.edges) {
    degrees.set(edge.from, (degrees.get(edge.from) ?? 0) + 1);
    degrees.set(edge.to, (degrees.get(edge.to) ?? 0) + 1);
  }

  const regions: BrainRegion[] = [];
  const nodes: BrainNode[] = [];
  for (const type of TYPE_ORDER) {
    const group = grouped.get(type) ?? [];
    if (group.length === 0) continue;
    const [regionX, regionY] = REGION[type];
    const baseX = centerX + regionX * brainX;
    const baseY = centerY + regionY * brainY;
    regions.push({ id: type, x: baseX, y: baseY, count: group.length });
    const spread = Math.min(0.29, 0.07 + Math.sqrt(group.length) * 0.013);
    for (const [index, node] of [...group].sort((a, b) => a.id.localeCompare(b.id)).entries()) {
      const seed = hash(node.id);
      const radius = Math.sqrt((index + 0.5) / group.length) * spread;
      const angle = index * GOLDEN_ANGLE + ((seed % 997) / 997) * 0.45;
      let nx = regionX + Math.cos(angle) * radius;
      let ny = regionY + Math.sin(angle) * radius * 0.78;
      const ellipse = nx * nx + ny * ny;
      if (ellipse > 0.92) {
        const scale = Math.sqrt(0.92 / ellipse);
        nx *= scale;
        ny *= scale;
      }
      const degree = degrees.get(node.id) ?? 0;
      nodes.push({
        ...node,
        x: centerX + nx * brainX,
        y: centerY + ny * brainY,
        z: 0,
        radius: Math.min(8, 3.2 + Math.sqrt(degree + 1) * 1.15),
        regionId: type,
      });
    }
  }
  return { width: options.width, height: options.height, nodes, edges: graph.edges, regions };
}
