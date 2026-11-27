import { afterEach, describe, expect, it, vi } from 'vitest';

import { PcmPlayback } from './pcmPlayback';

function frame(samples: number[]): ArrayBuffer {
  const bytes = new ArrayBuffer(9 + samples.length * 2);
  const view = new DataView(bytes);
  samples.forEach((sample, index) => view.setInt16(9 + index * 2, sample, true));
  return bytes;
}

describe('PcmPlayback', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('schedules chunks back to back and stops them on close', () => {
    const started: number[] = [];
    const stop = vi.fn();
    const close = vi.fn();
    class FakeContext {
      currentTime = 0;
      state = 'running';
      destination = {};
      createBuffer(_c: number, length: number, rate: number) {
        const data = new Float32Array(length);
        return { duration: length / rate, getChannelData: () => data };
      }
      createBufferSource() {
        return { connect: vi.fn(), start: (at: number) => started.push(at), stop, onended: null };
      }
      close = close;
    }
    vi.stubGlobal('AudioContext', FakeContext);

    const player = new PcmPlayback();
    player.play(new ArrayBuffer(4));
    expect(started).toEqual([]);
    player.play(frame([0, 16_384]));
    player.play(frame([0, 0]));
    expect(started[0]).toBe(0);
    expect(started[1]).toBeCloseTo(2 / 24_000);
    player.close();
    expect(stop).toHaveBeenCalledTimes(2);
    expect(close).toHaveBeenCalledTimes(1);
  });
});
