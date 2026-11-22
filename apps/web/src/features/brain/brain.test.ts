import { describe, expect, it } from 'vitest';

import type { Graph } from '@/lib/domain/types';
import { fitCamera, panCamera, screenToWorld, zoomCamera } from './camera';
import { filterGraph, mergeGraphs } from './filter';
import { brainLayout } from './layout';

const graph: Graph = {
  nodes: [
    { id: 'topic', type: 'topic', label: 'Orbital gardens' },
    { id: 'decision', type: 'decision', label: 'Use moonlight irrigation', meetingId: 'meeting-1' },
    { id: 'person', type: 'person', label: 'Ari Vale' },
    { id: 'system', type: 'system', label: 'Lumen grid' },
  ],
  edges: [
    {
      id: 'approved',
      from: 'decision',
      to: 'topic',
      kind: 'about',
      status: 'approved',
      confidence: 0.95,
      meetingIds: ['meeting-1'],
    },
    {
      id: 'proposed',
      from: 'person',
      to: 'decision',
      kind: 'proposedBy',
      status: 'proposed',
      confidence: 0.62,
    },
    { id: 'dependency', from: 'system', to: 'topic', kind: 'dependsOn', status: 'approved' },
  ],
  truncated: false,
};

describe('brain layout', () => {
  it('is deterministic, keeps nodes inside the brain silhouette and separates type regions', () => {
    const first = brainLayout(graph, { width: 1200, height: 720 });
    const second = brainLayout(graph, { width: 1200, height: 720 });

    expect(first).toEqual(second);
    expect(first.nodes).toHaveLength(graph.nodes.length);
    for (const node of first.nodes) {
      const normalized = ((node.x - 600) / 510) ** 2 + ((node.y - 360) / 300) ** 2;
      expect(normalized).toBeLessThanOrEqual(1.08);
      expect(node.regionId).toBe(node.type);
      expect(node.z).toBeGreaterThanOrEqual(-1);
      expect(node.z).toBeLessThanOrEqual(1);
    }
    expect(first.regions.map((region) => region.id)).toEqual(expect.arrayContaining(['topic', 'decision']));
  });

  it('lays out 2000 nodes without producing invalid coordinates', () => {
    const large: Graph = {
      nodes: Array.from({ length: 2000 }, (_, index) => ({
        id: `node-${index}`,
        type: index % 2 ? ('topic' as const) : ('system' as const),
        label: `Fictional node ${index}`,
      })),
      edges: [],
      truncated: false,
    };
    const result = brainLayout(large, { width: 1400, height: 800 });
    expect(result.nodes).toHaveLength(2000);
    expect(result.nodes.every((node) => Number.isFinite(node.x + node.y + node.z))).toBe(true);
  });
});

describe('brain filters and merging', () => {
  it('filters by search, type, relation kind and review status while retaining complete edges', () => {
    const result = filterGraph(graph, {
      query: 'moonlight',
      types: new Set(['decision']),
      kinds: new Set(['about']),
      statuses: new Set(['approved']),
    });
    expect(result.nodes.map((node) => node.id).sort()).toEqual(['decision', 'topic']);
    expect(result.edges.map((edge) => edge.id)).toEqual(['approved']);
  });

  it('merges an incremental focus expansion without duplicating nodes or edges', () => {
    const merged = mergeGraphs(graph, {
      nodes: [graph.nodes[0]!, { id: 'project', type: 'project', label: 'Aurora habitat' }],
      edges: [graph.edges[0]!, { id: 'new', from: 'project', to: 'topic', kind: 'about' }],
      truncated: true,
    });
    expect(merged.nodes).toHaveLength(5);
    expect(merged.edges).toHaveLength(4);
    expect(merged.truncated).toBe(true);
  });

  it('keeps only relation endpoints when relation filters are active', () => {
    const result = filterGraph(graph, {
      query: '',
      types: new Set(),
      kinds: new Set(['dependsOn']),
      statuses: new Set(['approved']),
    });
    expect(result.nodes.map((node) => node.id).sort()).toEqual(['system', 'topic']);
    expect(result.edges.map((edge) => edge.id)).toEqual(['dependency']);
  });
});

describe('brain camera', () => {
  it('fits, pans and zooms around the pointer without losing its world position', () => {
    const fitted = fitCamera({ width: 1000, height: 600 }, { width: 500, height: 300 });
    const pointer = { x: 320, y: 210 };
    const worldBefore = screenToWorld(pointer, fitted);
    const zoomed = zoomCamera(fitted, 1.5, pointer);
    const worldAfter = screenToWorld(pointer, zoomed);
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 6);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 6);
    expect(panCamera(zoomed, 20, -10)).toMatchObject({ x: zoomed.x + 20, y: zoomed.y - 10 });
  });

  it('clamps zoom to a usable range', () => {
    const camera = { x: 0, y: 0, zoom: 1, rotation: 0 };
    expect(zoomCamera(camera, 100, { x: 0, y: 0 }).zoom).toBe(3.2);
    expect(zoomCamera(camera, 0.001, { x: 0, y: 0 }).zoom).toBe(0.35);
  });
});
