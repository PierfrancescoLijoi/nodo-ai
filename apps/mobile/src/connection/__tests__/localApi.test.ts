import { mapSyncProgress } from '../localApi';

describe('sync progress mapping', () => {
  it('maps pushed to the copied count and preserves failed reasons', () => {
    expect(
      mapSyncProgress({
        state: 'running',
        total: 7,
        done: 5,
        pushed: 3,
        unchanged: 1,
        deleted: 1,
        failed: [{ meeting_id: 'm1', reason: 'conflict' }],
        message: 'Copying',
      }),
    ).toEqual({
      state: 'running',
      total: 7,
      done: 5,
      copied: 3,
      unchanged: 1,
      deleted: 1,
      failed: [{ meetingId: 'm1', reason: 'conflict' }],
      message: 'Copying',
    });
  });

  it('uses safe defaults for a partial response', () => {
    expect(mapSyncProgress({ state: 'idle' })).toMatchObject({ total: 0, copied: 0, failed: [] });
  });
});
