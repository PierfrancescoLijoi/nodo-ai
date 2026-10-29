export const VOICE_SAMPLE_RATE = 16_000;

export interface PcmFrame {
  offsetMs: number;
  pcm: Int16Array;
}

function pcm16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return clamped < 0 ? Math.round(clamped * 32_768) : Math.round(clamped * 32_767);
}

/** Stateful resampling is required because AudioWorklet blocks rarely align with 40 ms frames. */
export class StreamingPcm16Framer {
  private readonly samplesPerFrame: number;
  private readonly sourceStep: number;
  private sourcePosition = 0;
  private source = new Float32Array(0);
  private pending: number[] = [];
  private nextOffsetMs = 0;

  constructor(
    sourceRate: number,
    private readonly frameMs = 40,
  ) {
    if (sourceRate < VOICE_SAMPLE_RATE || frameMs < 10 || frameMs > 1_000) {
      throw new RangeError('Unsupported PCM framing parameters');
    }
    this.sourceStep = sourceRate / VOICE_SAMPLE_RATE;
    this.samplesPerFrame = (VOICE_SAMPLE_RATE * frameMs) / 1_000;
  }

  push(input: Float32Array): PcmFrame[] {
    const joined = new Float32Array(this.source.length + input.length);
    joined.set(this.source);
    joined.set(input, this.source.length);
    this.source = joined;

    while (this.sourcePosition < this.source.length) {
      const index = Math.floor(this.sourcePosition);
      this.pending.push(pcm16(this.source[index] ?? 0));
      this.sourcePosition += this.sourceStep;
    }
    const consumed = Math.min(this.source.length, Math.floor(this.sourcePosition));
    this.source = this.source.slice(consumed);
    this.sourcePosition -= consumed;

    const frames: PcmFrame[] = [];
    while (this.pending.length >= this.samplesPerFrame) {
      const pcm = Int16Array.from(this.pending.splice(0, this.samplesPerFrame));
      frames.push({ offsetMs: this.nextOffsetMs, pcm });
      this.nextOffsetMs += this.frameMs;
    }
    return frames;
  }
}

export interface PcmCapture {
  start(onFrame: (frame: PcmFrame) => void, onLevel: (level: number) => void): Promise<void>;
  stop(): Promise<void>;
}

export class BrowserPcmCapture implements PcmCapture {
  private stream?: MediaStream;
  private context?: AudioContext;
  private source?: MediaStreamAudioSourceNode;
  private worklet?: AudioWorkletNode;
  private sink?: GainNode;

  async start(onFrame: (frame: PcmFrame) => void, onLevel: (level: number) => void) {
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioWorkletNode === 'undefined') {
      throw Object.assign(new Error('Realtime audio unavailable'), { name: 'NotSupportedError' });
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.context = new AudioContext({ latencyHint: 'interactive' });
    await this.context.audioWorklet.addModule('/pcm-capture-worklet.js');
    const framer = new StreamingPcm16Framer(this.context.sampleRate);
    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, 'nodo-pcm-capture');
    this.sink = this.context.createGain();
    this.sink.gain.value = 0;
    this.worklet.port.onmessage = (event: MessageEvent<{ samples: Float32Array; level: number }>) => {
      onLevel(event.data.level);
      for (const frame of framer.push(event.data.samples)) onFrame(frame);
    };
    this.source.connect(this.worklet).connect(this.sink).connect(this.context.destination);
  }

  async stop() {
    const worklet = this.worklet;
    const source = this.source;
    const sink = this.sink;
    const stream = this.stream;
    const context = this.context;
    this.worklet = undefined;
    this.source = undefined;
    this.sink = undefined;
    this.stream = undefined;
    this.context = undefined;
    worklet?.disconnect();
    source?.disconnect();
    sink?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    if (context && context.state !== 'closed') await context.close();
  }
}
