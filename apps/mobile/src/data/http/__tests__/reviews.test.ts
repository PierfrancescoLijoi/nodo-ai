import type { ApiClient } from '../client';
import { createHttpRepository } from '../httpRepository';
import { mapGraph, mapReviews } from '../mappers';

function client(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    get: jest.fn().mockResolvedValue({}),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
    ...overrides,
  };
}

const inbox = {
  nodes: [
    { id: 'd1', type: 'decision', label: 'Keep it synchronous', status: null, meeting_id: 'm1' },
    { id: 'p1', type: 'person', label: 'Marco', status: null, meeting_id: null },
  ],
  edges: [
    {
      id: 'r1',
      source: 'd1',
      target: 'p1',
      kind: 'PROPOSED_BY',
      status: 'proposed',
      confidence: 0.9,
      valid_from: null,
      valid_to: null,
      evidence: [{ meeting_id: 'm1', segment_id: 's1' }],
    },
    { id: 'r2', source: 'd1', target: 'gone', kind: 'ABOUT', status: 'proposed', confidence: 0.5, evidence: [] },
  ],
  depth: 1,
  truncated: false,
};

describe('the review inbox mappers', () => {
  it('maps a proposal with both ends, its confidence and the meeting it came from', () => {
    expect(mapReviews(inbox)[0]).toEqual({
      id: 'r1',
      kind: 'proposedBy',
      from: { id: 'd1', type: 'decision', label: 'Keep it synchronous' },
      to: { id: 'p1', type: 'person', label: 'Marco' },
      confidence: 0.9,
      meetingId: 'm1',
    });
  });

  it('drops a proposal whose end is missing instead of showing half a sentence', () => {
    expect(mapReviews(inbox).map((item) => item.id)).toEqual(['r1']);
  });

  it('keeps the id of every graph edge so it can be decided', () => {
    expect(mapGraph(inbox).edges.map((edge) => edge.id)).toEqual(['r1', 'r2']);
  });
});

describe('HTTP review inbox', () => {
  it('lists proposals and sends the decision to the relation', async () => {
    const get = jest.fn().mockResolvedValue({
      nodes: [
        { id: 'a', type: 'topic', label: 'Ingestion' },
        { id: 'b', type: 'decision', label: 'Queue' },
      ],
      edges: [
        { id: 'r/1', source: 'b', target: 'a', kind: 'ABOUT', status: 'proposed', confidence: 0.8, evidence: [] },
      ],
    });
    const patch = jest.fn().mockResolvedValue({});
    const repository = createHttpRepository(client({ get, patch }), 'en', { mode: 'azure' });

    const items = await repository.listReviews();
    expect(get).toHaveBeenCalledWith('/v1/graph/reviews?limit=100');
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: 'r/1', kind: 'about' });

    await repository.reviewRelation('r/1', 'rejected');
    expect(patch).toHaveBeenCalledWith('/v1/graph/relations/r%2F1', { status: 'rejected' });
  });
});
