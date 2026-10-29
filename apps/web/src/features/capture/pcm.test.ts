import { afterEach, describe, expect, it, vi } from 'vitest';

import { BrowserPcmCapture, StreamingPcm16Framer } from './pcm';

afterEach(() => vi.unstubAllGlobals());

describe('StreamingPcm16Framer', () => {
  it('resamples browser audio to 16 kHz mono frames with monotonic offsets', () => {
    const framer = new StreamingPcm16Framer(48_000, 40);
    const input = new Float32Array(4_800).fill(0.5); // 100 ms at 48 kHz

    const frames = framer.push(input);

    expect(frames).toHaveLength(2);
    expect(frames.map((frame) => frame.offsetMs)).toEqual([0, 40]);
    expect(frames.every((frame) => frame.pcm.length === 640)).toBe(true);
    expect(frames[0]?.pcm[0]).toBeCloseTo(16_384, -1);
  });

  it('keeps fractional resampling state across worklet chunks', () => {
    const framer = new StreamingPcm16Framer(44_100, 20);
    const frames = [
      ...framer.push(new Float32Array(441).fill(0.25)),
      ...framer.push(new Float32Array(441).fill(0.25)),
    ];

    expect(frames).toHaveLength(1);
    expect(frames[0]?.offsetMs).toBe(0);
    expect(frames[0]?.pcm.length).toBe(320);
  });

  it('clamps samples before converting them to signed PCM16', () => {
    const framer = new StreamingPcm16Framer(16_000, 10);
    const input = new Float32Array(160);
    input.fill(2, 0, 80);
    input.fill(-2, 80);

    const [frame] = framer.push(input);

    expect(frame?.pcm[0]).toBe(32_767);
    expect(frame?.pcm[159]).toBe(-32_768);
  });
});

describe('BrowserPcmCapture', () => {
  it('connects the microphone worklet, emits framed audio and releases every resource', async () => {
    const stopTrack = vi.fn();
    const close = vi.fn().mockResolvedValue(undefined);
    const disconnect = vi.fn();
    const connect = vi.fn(function (this: object, next: object) {
      return next;
    });
    const source = { connect, disconnect };
    const sink = { connect, disconnect, gain: { value: 1 } };
    const worklet = {
      connect,
      disconnect,
      port: { onmessage: null as ((event: MessageEvent) => void) | null },
    };
    class FakeWorkletNode {
      constructor() {
        return worklet;
      }
    }
    class FakeAudioContext {
      sampleRate = 48_000;
      state = 'running';
      destination = {};
      audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
      createMediaStreamSource = () => source;
      createGain = () => sink;
      close = close;
    }
    vi.stubGlobal('AudioWorkletNode', FakeWorkletNode);
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: stopTrack }],
        } as unknown as MediaStream),
      },
    });
    const frames = vi.fn();
    const levels = vi.fn();
    const capture = new BrowserPcmCapture();

    await capture.start(frames, levels);
    worklet.port.onmessage?.({
      data: { samples: new Float32Array(1_920).fill(0.25), level: 0.4 },
    } as MessageEvent);
    await capture.stop();

    expect(frames).toHaveBeenCalledWith(expect.objectContaining({ offsetMs: 0 }));
    expect(levels).toHaveBeenCalledWith(0.4);
    expect(stopTrack).toHaveBeenCalled();
    expect(close).toHaveBeenCalled();
  });
});
