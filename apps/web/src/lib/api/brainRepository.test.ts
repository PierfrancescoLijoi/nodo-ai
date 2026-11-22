import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from './client';
import { createRepository } from './repository';

describe('Brain graph repository query', () => {
  it('loads the API maximum and keeps meeting evidence for detail links', async () => {
    const get = vi.fn(async () => ({
      nodes: [
        { id: 'decision-light', type: 'decision', label: 'Use moonlight irrigation' },
        { id: 'topic-orbit', type: 'topic', label: 'Orbital gardens' },
      ],
      edges: [
        {
          id: 'edge-approved',
          source: 'decision-light',
          target: 'topic-orbit',
          kind: 'ABOUT',
          status: 'approved',
          confidence: 0.96,
          evidence: [{ meeting_id: 'meeting-fictional' }, { meeting_id: 'meeting-fictional' }],
        },
      ],
      truncated: false,
    }));
    const repository = createRepository({ get } as unknown as ApiClient, { mode: 'local' });

    const graph = await repository.getGraph({ seedId: 'decision-light', depth: 2, limit: 200 });

    expect(get).toHaveBeenCalledWith(
      '/v1/graph/subgraph?depth=2&limit=200&include_proposed=true&seed_id=decision-light',
    );
    expect(graph.edges[0]?.meetingIds).toEqual(['meeting-fictional']);
  });
});
