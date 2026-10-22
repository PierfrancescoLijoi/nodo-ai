import { createMockRepository } from '../mock/mockRepository';

describe('mock speaker naming', () => {
  test('seeds two speakers and updates and clears a name in memory', async () => {
    const repository = createMockRepository('en');
    const before = await repository.getMeeting('m1');
    expect(before?.speakers).toHaveLength(2);

    const named = await repository.nameSpeaker('m1', 'S1', 'Ada Lovelace');
    expect(named.speakers.find((speaker) => speaker.id === 'S1')?.name).toBe('Ada Lovelace');
    await expect(repository.getMeeting('m1')).resolves.toEqual(named);

    const cleared = await repository.nameSpeaker('m1', 'S1', null);
    expect(cleared.speakers.find((speaker) => speaker.id === 'S1')?.name).toBeNull();
  });

  test.each(['', 'one two three four five six', 'Ada_'])('rejects invalid name %j with 422', async (name) => {
    const repository = createMockRepository('en');
    await expect(repository.nameSpeaker('m1', 'S1', name)).rejects.toMatchObject({ status: 422 });
  });

  test('returns 404 for an unknown meeting or speaker', async () => {
    const repository = createMockRepository('en');
    await expect(repository.nameSpeaker('nope', 'S1', 'Ada')).rejects.toMatchObject({ status: 404 });
    await expect(repository.nameSpeaker('m1', 'S9', 'Ada')).rejects.toMatchObject({ status: 404 });
  });

  test('returns 409 when the meeting cannot be edited', async () => {
    const repository = createMockRepository('en');
    const meeting = await repository.getMeeting('m1');
    if (!meeting) throw new Error('Missing test meeting');
    meeting.state = 'transcribing';

    await expect(repository.nameSpeaker('m1', 'S1', 'Ada')).rejects.toEqual(expect.objectContaining({ status: 409 }));
  });
});
