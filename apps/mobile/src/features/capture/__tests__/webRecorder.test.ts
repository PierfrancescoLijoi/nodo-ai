import { BrowserRecorder, type MediaRecorderLike } from '../webRecorder';

class FakeMediaRecorder implements MediaRecorderLike {
  state = 'inactive';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  start = jest.fn(() => {
    this.state = 'recording';
  });
  pause = jest.fn(() => {
    this.state = 'paused';
  });
  resume = jest.fn(() => {
    this.state = 'recording';
  });
  stop = jest.fn(() => {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['audio']) });
    this.onstop?.();
  });
}

describe('BrowserRecorder', () => {
  it('records chunks, pauses, resumes, and uses wall-clock elapsed time', async () => {
    let now = 1_000;
    const mediaRecorder = new FakeMediaRecorder();
    const track = { stop: jest.fn() };
    const recorder = new BrowserRecorder({
      now: () => now,
      getUserMedia: async () => ({ getTracks: () => [track] }) as unknown as MediaStream,
      createMediaRecorder: () => mediaRecorder,
      startTimer: () => 1,
      stopTimer: jest.fn(),
      createLevelMeter: () => jest.fn(),
    });

    await recorder.start();
    now = 4_400;
    recorder.refreshElapsed();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'recording', seconds: 3 });
    recorder.pause();
    now = 9_000;
    recorder.resume();
    now = 11_100;
    recorder.refreshElapsed();
    expect(recorder.getSnapshot().seconds).toBe(5);
    recorder.stop();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'saved', error: null });
    expect(recorder.getSnapshot().audio?.size).toBeGreaterThan(0);
    expect(mediaRecorder.start).toHaveBeenCalledWith(1000);
    expect(track.stop).toHaveBeenCalled();
  });

  it.each([
    ['NotAllowedError', 'permission'],
    ['NotFoundError', 'noMicrophone'],
    ['OtherError', 'recorder'],
  ] as const)('maps %s to %s and permits retry', async (name, expected) => {
    let attempt = 0;
    const recorder = new BrowserRecorder({
      now: Date.now,
      getUserMedia: async () => {
        attempt += 1;
        if (attempt === 1) throw Object.assign(new Error('no'), { name });
        return { getTracks: () => [] } as unknown as MediaStream;
      },
      createMediaRecorder: () => new FakeMediaRecorder(),
      startTimer: () => 1,
      stopTimer: jest.fn(),
      createLevelMeter: () => jest.fn(),
    });
    await recorder.start();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'error', error: expected });
    await recorder.start();
    expect(recorder.getSnapshot().state).toBe('recording');
  });

  it('moves to an actionable error when MediaRecorder fails', async () => {
    const mediaRecorder = new FakeMediaRecorder();
    const recorder = new BrowserRecorder({
      now: Date.now,
      getUserMedia: async () => ({ getTracks: () => [] }) as unknown as MediaStream,
      createMediaRecorder: () => mediaRecorder,
      startTimer: () => 1,
      stopTimer: jest.fn(),
      createLevelMeter: () => jest.fn(),
    });
    await recorder.start();
    mediaRecorder.onerror?.();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'error', error: 'recorder' });
  });
});
