import { ApiError, type ApiClient } from '../client';
import { createHttpRepository } from '../httpRepository';
import fixture from './fixtures/api.json';

function client(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    get: jest.fn().mockResolvedValue({ items: [] }),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
    ...overrides,
  };
}

describe('HTTP recording lifecycle', () => {
  it('creates, uploads, completes, reads state, and deletes a meeting', async () => {
    const post = jest
      .fn()
      .mockResolvedValueOnce({ id: 'meeting-1' })
      .mockResolvedValueOnce({ upload_url: 'http://127.0.0.1/upload/one' })
      .mockResolvedValueOnce({ state: 'transcribing' });
    const get = jest.fn().mockResolvedValue({ state: 'extracting' });
    const remove = jest.fn().mockResolvedValue(undefined);
    const fetchImpl = jest.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const repository = createHttpRepository(client({ post, get, delete: remove }), 'pl', {
      mode: 'local',
      fetchImpl,
      createIdempotencyKey: () => 'fresh-key',
    });

    await expect(repository.createMeeting({ locale: 'pl', retentionDays: 30 })).resolves.toEqual({ id: 'meeting-1' });
    await expect(repository.uploadAudio('meeting-1', new Blob(['audio']))).resolves.toBeUndefined();
    await expect(repository.getMeetingState('meeting-1')).resolves.toBe('extracting');
    await expect(repository.deleteMeeting('meeting-1')).resolves.toBeUndefined();

    expect(post).toHaveBeenNthCalledWith(
      1,
      '/v1/meetings',
      {
        recording_attestation: {
          notice_version: '2026-10-v1',
          locale: 'pl',
          retention_policy_id: 'retention-30d',
          acknowledged: true,
        },
      },
      { headers: { 'Idempotency-Key': 'fresh-key' } },
    );
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1/upload/one',
      expect.objectContaining({ method: 'PUT', body: expect.any(Blob) }),
    );
    expect((fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined)?.headers).toBeUndefined();
    expect(post).toHaveBeenNthCalledWith(3, '/v1/meetings/meeting-1/complete', {});
    expect(remove).toHaveBeenCalledWith('/v1/meetings/meeting-1');
  });
});

describe('HTTP speaker naming', () => {
  it('patches the encoded speaker path and maps the updated meeting', async () => {
    const patch = jest.fn().mockResolvedValue(fixture.meeting);
    const repository = createHttpRepository(client({ patch }), 'en');

    await expect(repository.nameSpeaker('meeting/id', 'S 1', 'Ada')).resolves.toEqual(
      expect.objectContaining({
        id: 'm1',
        speakers: expect.arrayContaining([{ id: 'S1', name: 'Ada', segments: 3, talkMs: 42000 }]),
      }),
    );
    expect(patch).toHaveBeenCalledWith('/v1/meetings/meeting%2Fid/speakers/S%201', { name: 'Ada' });
  });

  it('sends null to clear a name', async () => {
    const patch = jest.fn().mockResolvedValue(fixture.meeting);
    const repository = createHttpRepository(client({ patch }), 'en');

    await repository.nameSpeaker('m1', 'S1', null);

    expect(patch).toHaveBeenCalledWith('/v1/meetings/m1/speakers/S1', { name: null });
  });

  it.each([404, 409, 422])('preserves API errors with status %s', async (status) => {
    const error = new ApiError('No', status, 'speaker_error');
    const repository = createHttpRepository(client({ patch: jest.fn().mockRejectedValue(error) }), 'en');

    await expect(repository.nameSpeaker('m1', 'S1', 'Ada')).rejects.toBe(error);
  });
});
