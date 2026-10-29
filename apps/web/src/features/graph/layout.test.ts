import { describe, expect, it } from 'vitest';

import type { Graph } from '@/lib/domain/types';
import { radialLayout } from './layout';

const node = (id: string) => ({ id, type: 'topic' as const, label: id });
const edge = (id: string, from: string, to: string, extra: Partial<Graph['edges'][number]> = {}) => ({
  id,
  from,
  to,
  kind: 'about' as const,
  ...extra,
});
const options = { width: 640, height: 420, maxNodes: 4 };

describe('radialLayout', () => {
  it('puts the selected node in the middle and its neighbours around it', () => {
    const graph: Graph = {
      nodes: ['a', 'b', 'c', 'd'].map(node),
      edges: [edge('1', 'a', 'b'), edge('2', 'c', 'a'), edge('3', 'b', 'd')],
      truncated: false,
    };
    const result = radialLayout(graph, 'a', options);
    expect(result.nodes[0]).toMatchObject({ id: 'a', x: 320, y: 210 });
    expect(result.nodes.map((n) => n.id).sort()).toEqual(['a', 'b', 'c']);
    expect(result.leftOut.map((n) => n.id)).toEqual(['d']);
    expect(result.edges.map((e) => e.id).sort()).toEqual(['1', '2']);
  });

  it('keeps every node inside the frame', () => {
    const graph: Graph = {
      nodes: Array.from({ length: 12 }, (_, i) => node(`n${i}`)),
      edges: Array.from({ length: 11 }, (_, i) => edge(`e${i}`, 'n0', `n${i + 1}`)),
      truncated: false,
    };
    const result = radialLayout(graph, 'n0', { ...options, maxNodes: 12 });
    for (const placed of result.nodes) {
      expect(placed.x).toBeGreaterThanOrEqual(0);
      expect(placed.x).toBeLessThanOrEqual(640);
      expect(placed.y).toBeGreaterThanOrEqual(0);
      expect(placed.y).toBeLessThanOrEqual(420);
    }
    expect(result.nodes).toHaveLength(12);
  });

  it('shows the strongest links first when it cannot show them all: approved, then by confidence', () => {
    const graph: Graph = {
      nodes: ['a', 'weak', 'strong', 'approved'].map(node),
      edges: [
        edge('1', 'a', 'weak', { confidence: 0.2, status: 'proposed' }),
        edge('2', 'a', 'strong', { confidence: 0.9, status: 'proposed' }),
        edge('3', 'a', 'approved', { confidence: 0.1, status: 'approved' }),
      ],
      truncated: false,
    };
    const result = radialLayout(graph, 'a', { ...options, maxNodes: 3 });
    expect(result.nodes.map((n) => n.id)).toEqual(['a', 'approved', 'strong']);
    expect(result.leftOut.map((n) => n.id)).toEqual(['weak']);
  });

  it('falls back to the first node when the selection is gone, and copes with an empty graph', () => {
    const graph: Graph = { nodes: [node('a'), node('b')], edges: [edge('1', 'a', 'b')], truncated: false };
    expect(radialLayout(graph, 'missing', options).nodes[0]?.id).toBe('a');
    expect(radialLayout({ nodes: [], edges: [], truncated: false }, 'x', options)).toEqual({
      nodes: [],
      edges: [],
      leftOut: [],
    });
  });

  it('is deterministic', () => {
    const graph: Graph = {
      nodes: ['a', 'b', 'c'].map(node),
      edges: [edge('1', 'a', 'b'), edge('2', 'a', 'c')],
      truncated: false,
    };
    expect(radialLayout(graph, 'a', options)).toEqual(radialLayout(graph, 'a', options));
  });
});
