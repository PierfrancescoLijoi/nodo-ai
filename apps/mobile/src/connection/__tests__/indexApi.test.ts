import { createLocalEngineApi, mapIndexProgress } from '../localApi';

describe('search index checks', () => {
  it('reads the progress object and files unknown kinds under "other"', () => {
    const progress = mapIndexProgress({
      state: 'done',
      mode: 'verify',
      checked: 4,
      skipped: 1,
      repaired: 0,
      findings: [
        { kind: 'missing_from_index', meeting_id: 'm1' },
        { kind: 'something_new', meeting_id: 'm2' },
      ],
      failed: [{ meeting_id: 'm3', reason: 'ConnectionError' }],
      message: '',
    });
    expect(progress).toMatchObject({ state: 'done', mode: 'verify', checked: 4, skipped: 1 });
    expect(progress.findings).toEqual([
      { kind: 'missing_from_index', meetingId: 'm1' },
      { kind: 'other', meetingId: 'm2' },
    ]);
    expect(progress.failed).toEqual([{ meetingId: 'm3', reason: 'ConnectionError' }]);
  });

  it('treats an unreadable answer as failed rather than idle', () => {
    expect(mapIndexProgress(null).state).toBe('failed');
  });

  it('starts a run with its mode and reads the progress back', async () => {
    const post = jest.fn().mockResolvedValue({ state: 'running', mode: 'repair' });
    const get = jest.fn().mockResolvedValue({ state: 'idle' });
    const api = createLocalEngineApi({ get, post, patch: jest.fn(), delete: jest.fn() });
    expect((await api.startIndex('repair')).state).toBe('running');
    expect(post).toHaveBeenCalledWith('/v1/local/index', { mode: 'repair' });
    expect((await api.getIndex()).state).toBe('idle');
    expect(get).toHaveBeenCalledWith('/v1/local/index');
  });
});
