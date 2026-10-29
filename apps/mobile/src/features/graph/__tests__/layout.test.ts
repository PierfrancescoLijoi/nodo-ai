import type { Graph } from '@/data/types';
import { radialLayout } from '../layout';

const graph: Graph = {
  nodes: Array.from({ length: 15 }, (_, index) => ({ id: `n${index}`, type: 'topic', label: `Node ${index}` })),
  edges: Array.from({ length: 14 }, (_, index) => ({
    from: 'n0',
    to: `n${index + 1}`,
    kind: 'about',
    status: index % 2 ? 'proposed' : 'approved',
    confidence: 1 - index / 20,
  })),
};

describe('radialLayout', () => {
  it('is deterministic, centres the selected node, and caps neighbours', () => {
    const result = radialLayout(graph, 'n0', { width: 340, height: 300, maxNodes: 12 });
    expect(result).toEqual(radialLayout(graph, 'n0', { width: 340, height: 300, maxNodes: 12 }));
    expect(result.nodes).toHaveLength(12);
    expect(result.nodes.find((node) => node.id === 'n0')).toMatchObject({ x: 170, y: 150 });
    expect(result.leftOut).toHaveLength(3);
  });

  it('keeps centres separated and inside the canvas', () => {
    const { nodes } = radialLayout(graph, 'n0', { width: 340, height: 300, maxNodes: 12 });
    for (const [index, node] of nodes.entries()) {
      expect(node.x).toBeGreaterThanOrEqual(24);
      expect(node.x).toBeLessThanOrEqual(316);
      expect(node.y).toBeGreaterThanOrEqual(24);
      expect(node.y).toBeLessThanOrEqual(276);
      for (const other of nodes.slice(index + 1)) {
        expect(Math.hypot(node.x - other.x, node.y - other.y)).toBeGreaterThanOrEqual(48);
      }
    }
  });
});
