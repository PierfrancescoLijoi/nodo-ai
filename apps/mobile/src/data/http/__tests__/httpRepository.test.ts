import { ApiError, type ApiClient } from '../client';
import { createHttpRepository } from '../httpRepository';
import fixture from './fixtures/api.json';

function client(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    get: jest.fn().mockResolvedValue({ items: [] }),
    post: jest.fn(),
    patch: jest.fn(),
    ...overrides,
  };
}

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
