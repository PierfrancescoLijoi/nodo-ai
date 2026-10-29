import { describe, expect, it, vi } from 'vitest';

import { ApiError, type ApiClient } from './client';
import { createRepository, NOTICE_VERSION } from './repository';

const meetingItem = (id: string) => ({
  id,
  title: `Meeting ${id}`,
  state: 'ready',
  created_at: '2026-10-21T09:00:00Z',
  duration_ms: 90_000,
  summary: null,
  people: ['Marco'],
  topics: ['Ingestion'],
  decision_count: 2,
  action_count: 1,
});

const detail = {
  id: 'm1',
  title: 'Planning',
  state: 'review',
  created_at: '2026-10-21T09:00:00Z',
  duration_ms: 60_000,
  summary: 'Summary',
  insights: {
    people: [{ id: 'p1', label: 'Sara' }],
    topics: [{ id: 't1', label: 'Queue' }],
    decisions: [{ id: 'd1', label: 'Use a queue', status: 'proposed', effective_at: null }],
    actions: [
      { id: 'a1', label: 'Benchmark', owner_label: 'Sara', due_at: '2026-10-28', status: 'proposed' },
    ],
  },
  speakers: [{ id: 'S1', name: null, segments: 2, talk_ms: 4000 }],
};

function fakeClient(
  routes: Record<string, unknown | ((body?: unknown) => unknown)>,
): ApiClient & { calls: string[] } {
  const calls: string[] = [];
  const answer = async (method: string, path: string, body?: unknown) => {
    calls.push(`${method} ${path}`);
    const key = Object.keys(routes).find((candidate) => `${method} ${path}`.startsWith(candidate));
    if (!key) throw new ApiError('not found', 404, 'not_found');
    const route = routes[key];
    const value = typeof route === 'function' ? (route as (b?: unknown) => unknown)(body) : route;
    if (value instanceof Error) throw value;
    return value;
  };
  return {
    calls,
    get: (path: string) => answer('GET', path) as never,
    post: (path: string, body: unknown) => answer('POST', path, body) as never,
    patch: (path: string, body: unknown) => answer('PATCH', path, body) as never,
    delete: async (path: string) => void (await answer('DELETE', path)),
    blob: async (path: string) => (await answer('BLOB', path)) as Blob,
  };
}

describe('createRepository', () => {
  it('creates a meeting with the attestation, the retention policy and an idempotency key', async () => {
    const post = vi.fn(async () => ({ id: 'm1' }));
    const client = { ...fakeClient({}), post } as unknown as ApiClient;
    const repository = createRepository(client, {
      mode: 'local',
      createIdempotencyKey: () => 'key-12345678',
    });
    await expect(
      repository.createMeeting({ locale: 'it', retentionDays: 90, title: 'Planning' }),
    ).resolves.toEqual({
      id: 'm1',
    });
    expect(post).toHaveBeenCalledWith(
      '/v1/meetings',
      {
        recording_attestation: {
          notice_version: NOTICE_VERSION,
          locale: 'it',
          retention_policy_id: 'retention-90d',
          acknowledged: true,
        },
        title: 'Planning',
      },
      { headers: { 'Idempotency-Key': 'key-12345678' } },
    );
  });

  it('uploads to the session link then completes the meeting', async () => {
    const put = vi.fn(async () => new Response(null, { status: 204 }));
    const client = fakeClient({
      'POST /v1/meetings/m1/upload-sessions': { upload_url: 'http://127.0.0.1:8765/v1/local/uploads/t' },
      'POST /v1/meetings/m1/complete': {},
    });
    const repository = createRepository(client, { mode: 'local', fetchImpl: put as unknown as typeof fetch });
    const progress = vi.fn();
    await repository.uploadAudio('m1', new Blob(['a']), progress);
    expect(put).toHaveBeenCalledWith(
      'http://127.0.0.1:8765/v1/local/uploads/t',
      expect.objectContaining({ method: 'PUT' }),
    );
    expect(client.calls).toEqual(['POST /v1/meetings/m1/upload-sessions', 'POST /v1/meetings/m1/complete']);
    expect(progress).toHaveBeenLastCalledWith(1);
  });

  it('lists meetings across pages and stops at the last one', async () => {
    let page = 0;
    const client = fakeClient({
      'GET /v1/meetings': () => {
        page += 1;
        return page === 1
          ? { items: [meetingItem('a')], next_cursor: 'c2' }
          : { items: [meetingItem('b')], next_cursor: null };
      },
    });
    const meetings = await createRepository(client, { mode: 'azure' }).listMeetings();
    expect(meetings.map((meeting) => meeting.id)).toEqual(['a', 'b']);
    expect(client.calls[1]).toContain('cursor=c2');
    expect(meetings[0]).toMatchObject({
      durationMin: 2,
      summary: '',
      decisionCount: 2,
      participants: ['Marco'],
    });
  });

  it('reads a meeting with all transcript pages', async () => {
    const segment = (n: number) => ({ id: `s${n}`, speaker_id: 'S1', start_ms: n, text: `t${n}` });
    const client = fakeClient({
      'GET /v1/meetings/m1/transcript?limit=200&offset=0': {
        items: Array.from({ length: 200 }, (_, n) => segment(n)),
      },
      'GET /v1/meetings/m1/transcript?limit=200&offset=200': { items: [segment(200)] },
      'GET /v1/meetings/m1': detail,
    });
    const meeting = await createRepository(client, { mode: 'azure' }).getMeeting('m1');
    expect(meeting?.segments).toHaveLength(201);
    expect(meeting).toMatchObject({
      title: 'Planning',
      participants: ['Sara'],
      decisions: ['Use a queue'],
      actions: [{ id: 'a1', text: 'Benchmark', owner: 'Sara', dueDate: '2026-10-28' }],
      speakers: [{ id: 'S1', name: null, talkMs: 4000 }],
    });
  });

  it('returns null for a meeting that is not there, and rethrows anything else', async () => {
    const missing = createRepository(fakeClient({}), { mode: 'azure' });
    await expect(missing.getMeeting('nope')).resolves.toBeNull();
    const broken = createRepository(
      fakeClient({ 'GET /v1/meetings/m1': new ApiError('boom', 500, 'internal_error') }),
      { mode: 'azure' },
    );
    await expect(broken.getMeeting('m1')).rejects.toMatchObject({ status: 500 });
  });

  it('refuses a response that does not match the contract', async () => {
    const client = fakeClient({ 'GET /v1/meetings': { items: [{ id: 'a' }] } });
    await expect(createRepository(client, { mode: 'azure' }).listMeetings()).rejects.toMatchObject({
      code: 'invalid_response',
    });
  });

  it('names a speaker and returns the refreshed meeting', async () => {
    const patch = vi.fn(async () => detail);
    const client = {
      ...fakeClient({ 'GET /v1/meetings/m1/transcript': { items: [] } }),
      patch,
    } as unknown as ApiClient;
    const meeting = await createRepository(client, { mode: 'azure' }).nameSpeaker('m1', 'S1', 'Marco Rossi');
    expect(patch).toHaveBeenCalledWith('/v1/meetings/m1/speakers/S1', { name: 'Marco Rossi' });
    expect(meeting.id).toBe('m1');
  });

  it('asks with the interface language and maps the sources', async () => {
    const post = vi.fn(async () => ({
      answer: 'It moved to a queue.',
      answerability: 'supported',
      degraded: true,
      citations: [
        { meeting_id: 'm1', segment_id: 's1', speaker_id: 'S2', start_ms: 5000, end_ms: 6000, quote: 'q' },
      ],
    }));
    const client = { ...fakeClient({}), post } as unknown as ApiClient;
    const answer = await createRepository(client, { mode: 'azure' }).ask('What changed?', 'da');
    expect(post).toHaveBeenCalledWith(
      '/v1/query',
      { question: 'What changed?', ui_locale: 'da' },
      expect.any(Object),
    );
    expect(answer).toEqual({
      answerability: 'supported',
      text: 'It moved to a queue.',
      degraded: true,
      citations: [{ meetingId: 'm1', segmentId: 's1', startMs: 5000, speaker: 'S2', quote: 'q' }],
    });
  });

  it('maps the graph and the review inbox, dropping an edge whose end is missing', async () => {
    const graph = {
      nodes: [
        { id: 'n1', type: 'decision', label: 'Use a queue', meeting_id: 'm1' },
        { id: 'n2', type: 'person', label: 'Marco' },
      ],
      edges: [
        {
          id: 'e1',
          source: 'n1',
          target: 'n2',
          kind: 'PROPOSED_BY',
          status: 'proposed',
          confidence: 0.9,
          evidence: [{ meeting_id: 'm1', segment_id: 's' }],
        },
        { id: 'e2', source: 'n1', target: 'missing', kind: 'ABOUT', confidence: 0.5 },
      ],
      truncated: true,
    };
    const client = fakeClient({ 'GET /v1/graph/subgraph': graph, 'GET /v1/graph/reviews': graph });
    const repository = createRepository(client, { mode: 'azure' });
    const loaded = await repository.getGraph({ seedId: 'n1' });
    expect(client.calls[0]).toContain('seed_id=n1');
    expect(loaded.truncated).toBe(true);
    expect(loaded.edges[0]).toMatchObject({
      from: 'n1',
      to: 'n2',
      kind: 'proposedBy',
      status: 'proposed',
      confidence: 0.9,
    });
    expect(loaded.nodes[0]).toMatchObject({ meetingId: 'm1' });
    const inbox = await repository.listReviews();
    expect(inbox).toEqual([
      {
        id: 'e1',
        kind: 'proposedBy',
        from: { id: 'n1', type: 'decision', label: 'Use a queue' },
        to: { id: 'n2', type: 'person', label: 'Marco' },
        confidence: 0.9,
        meetingId: 'm1',
      },
    ]);
  });

  it('decides a proposal and lists the latest changes', async () => {
    const patch = vi.fn(async () => ({}));
    const client = {
      ...fakeClient({
        'GET /v1/graph/changes': {
          items: [
            { id: 'c1', new_label: 'Queue', old_label: 'Sync', meeting_id: 'm1', at: '2026-10-21T00:00:00Z' },
          ],
        },
      }),
      patch,
    } as unknown as ApiClient;
    const repository = createRepository(client, { mode: 'azure' });
    await repository.reviewRelation('e/1', 'approved');
    expect(patch).toHaveBeenCalledWith('/v1/graph/relations/e%2F1', { status: 'approved' });
    await expect(repository.listChanges()).resolves.toEqual([
      { id: 'c1', newLabel: 'Queue', oldLabel: 'Sync', meetingId: 'm1', date: '2026-10-21T00:00:00Z' },
    ]);
  });

  it('runs the export: start, poll, download', async () => {
    const client = fakeClient({
      'POST /v1/export': { job_id: 'j1', status: 'queued' },
      'GET /v1/export/j1': { job_id: 'j1', status: 'ready', download_path: '/x' },
      'BLOB /v1/export/j1/download': new Blob(['{}']),
    });
    const repository = createRepository(client, { mode: 'azure' });
    await expect(repository.startExport()).resolves.toEqual({ id: 'j1', status: 'queued' });
    await expect(repository.getExport('j1')).resolves.toEqual({ id: 'j1', status: 'ready' });
    expect(await (await repository.downloadExport('j1')).text()).toBe('{}');
  });

  it('reads a meeting state and deletes a meeting', async () => {
    const client = fakeClient({
      'GET /v1/meetings/m1': { state: 'transcribing' },
      'DELETE /v1/meetings/m1': undefined,
    });
    const repository = createRepository(client, { mode: 'azure' });
    await expect(repository.getMeetingState('m1')).resolves.toBe('transcribing');
    await repository.deleteMeeting('m1');
    expect(client.calls).toContain('DELETE /v1/meetings/m1');
  });
});
