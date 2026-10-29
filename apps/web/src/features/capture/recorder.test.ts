import { describe, expect, it, vi } from 'vitest';

import {
  BrowserRecorder,
  recorderError,
  type MediaRecorderLike,
  type RecorderDependencies,
} from './recorder';

function harness(overrides: Partial<RecorderDependencies> = {}) {
  let clock = 0;
  let tick: () => void = () => undefined;
  const track = { stop: vi.fn() };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  const media: MediaRecorderLike & { emit(size: number): void; finish(): void; fail(): void } = {
    ondataavailable: null,
    onstop: null,
    onerror: null,
    start: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(() => media.onstop?.()),
    emit: (size) =>
      media.ondataavailable?.({ data: new Blob([new Uint8Array(size)], { type: 'audio/webm' }) }),
    finish: () => media.onstop?.(),
    fail: () => media.onerror?.(),
  };
  const stopMeter = vi.fn();
  const deps: RecorderDependencies = {
    now: () => clock,
    getUserMedia: async () => stream,
    createMediaRecorder: () => media,
    startTimer: (callback) => {
      tick = callback;
      return 1;
    },
    stopTimer: vi.fn(),
    createLevelMeter: () => stopMeter,
    ...overrides,
  };
  return {
    recorder: new BrowserRecorder(deps),
    media,
    track,
    stopMeter,
    advance: (ms: number) => {
      clock += ms;
      tick();
    },
  };
}

describe('BrowserRecorder', () => {
  it('records: asks for the microphone, runs the timer, saves the audio, releases the microphone', async () => {
    const { recorder, media, advance, track, stopMeter } = harness();
    expect(recorder.getSnapshot().state).toBe('idle');
    await recorder.start();
    expect(recorder.getSnapshot().state).toBe('recording');
    expect(media.start).toHaveBeenCalledWith(1000);
    media.emit(10);
    advance(3200);
    expect(recorder.getSnapshot().seconds).toBe(3);
    recorder.stop();
    const saved = recorder.getSnapshot();
    expect(saved.state).toBe('saved');
    expect(saved.audio?.size).toBe(10);
    expect(saved.audio?.type).toBe('audio/webm');
    expect(track.stop).toHaveBeenCalled();
    expect(stopMeter).toHaveBeenCalled();
  });

  it('does not count paused time', async () => {
    const { recorder, advance } = harness();
    await recorder.start();
    advance(2000);
    recorder.pause();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'paused', seconds: 2 });
    advance(60_000);
    recorder.resume();
    advance(1000);
    expect(recorder.getSnapshot()).toMatchObject({ state: 'recording', seconds: 3 });
  });

  it('ignores controls that do not apply to the current state', async () => {
    const { recorder, media } = harness();
    recorder.pause();
    recorder.resume();
    recorder.stop();
    expect(recorder.getSnapshot().state).toBe('idle');
    await recorder.start();
    recorder.resume();
    expect(media.resume).not.toHaveBeenCalled();
  });

  it.each([
    ['NotAllowedError', 'permission'],
    ['SecurityError', 'permission'],
    ['NotFoundError', 'noMicrophone'],
    ['DevicesNotFoundError', 'noMicrophone'],
    ['AbortError', 'recorder'],
  ])('a %s from the browser is shown as %s', async (name, expected) => {
    const { recorder } = harness({
      getUserMedia: async () => {
        throw Object.assign(new Error(name), { name });
      },
    });
    await recorder.start();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'error', error: expected });
  });

  it('an error from the recorder itself stops everything and can be retried', async () => {
    const { recorder, media, track } = harness();
    await recorder.start();
    media.fail();
    expect(recorder.getSnapshot()).toMatchObject({ state: 'error', error: 'recorder' });
    expect(track.stop).toHaveBeenCalled();
    await recorder.start();
    expect(recorder.getSnapshot().state).toBe('recording');
  });

  it('notifies subscribers and lets them go', async () => {
    const { recorder } = harness();
    const listener = vi.fn();
    const unsubscribe = recorder.subscribe(listener);
    await recorder.start();
    expect(listener).toHaveBeenCalled();
    const calls = listener.mock.calls.length;
    unsubscribe();
    recorder.stop();
    expect(listener).toHaveBeenCalledTimes(calls);
  });

  it('dispose releases the microphone', async () => {
    const { recorder, track } = harness();
    await recorder.start();
    recorder.dispose();
    expect(track.stop).toHaveBeenCalled();
  });
});

describe('recorderError', () => {
  it('maps anything else to a generic recorder error', () => {
    expect(recorderError('nope')).toBe('recorder');
    expect(recorderError(new Error('x'))).toBe('recorder');
  });
});
