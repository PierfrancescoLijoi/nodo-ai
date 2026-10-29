// The microphone recorder as a small state machine with injectable browser parts, so every transition is
// testable without a microphone. The component reads it through useSyncExternalStore.
export type RecorderState = 'idle' | 'requesting' | 'recording' | 'paused' | 'saved' | 'error';
export type RecorderError = 'permission' | 'noMicrophone' | 'recorder';

export interface RecorderSnapshot {
  state: RecorderState;
  seconds: number;
  audio: Blob | null;
  error: RecorderError | null;
  /** 0 to 1, from the microphone's own signal. */
  level: number;
}

export interface MediaRecorderLike {
  ondataavailable: ((event: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: (() => void) | null;
  start(timeslice?: number): void;
  pause(): void;
  resume(): void;
  stop(): void;
}

export interface RecorderDependencies {
  now(): number;
  getUserMedia(): Promise<MediaStream>;
  createMediaRecorder(stream: MediaStream): MediaRecorderLike;
  startTimer(callback: () => void): number;
  stopTimer(id: number): void;
  createLevelMeter(stream: MediaStream, onLevel: (level: number) => void): () => void;
}

const CHUNK_MS = 1000;
const TICK_MS = 250;
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
const INITIAL: RecorderSnapshot = { state: 'idle', seconds: 0, audio: null, error: null, level: 0 };

export function recorderError(error: unknown): RecorderError {
  const name = error instanceof Error ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'permission';
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'noMicrophone';
  return 'recorder';
}

export function preferredMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  return MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
}

export function canRecord(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'
  );
}

function levelMeter(stream: MediaStream, onLevel: (level: number) => void): () => void {
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.frequencyBinCount);
  let frame = 0;
  const read = () => {
    analyser.getByteTimeDomainData(samples);
    const sum = samples.reduce((total, sample) => total + Math.abs(sample - 128), 0);
    onLevel(Math.min(1, (sum / samples.length / 128) * 4));
    frame = requestAnimationFrame(read);
  };
  frame = requestAnimationFrame(read);
  return () => {
    cancelAnimationFrame(frame);
    source.disconnect();
    void context.close();
  };
}

export function browserDependencies(): RecorderDependencies {
  return {
    now: Date.now,
    getUserMedia: async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw Object.assign(new Error('Microphone unavailable'), { name: 'NotFoundError' });
      }
      return navigator.mediaDevices.getUserMedia({ audio: true });
    },
    createMediaRecorder: (stream) => {
      const mimeType = preferredMimeType();
      return new MediaRecorder(stream, mimeType ? { mimeType } : undefined) as unknown as MediaRecorderLike;
    },
    startTimer: (callback) => window.setInterval(callback, TICK_MS),
    stopTimer: (id) => window.clearInterval(id),
    createLevelMeter: levelMeter,
  };
}

export class BrowserRecorder {
  private snapshot: RecorderSnapshot = INITIAL;
  private readonly listeners = new Set<() => void>();
  private mediaRecorder?: MediaRecorderLike;
  private stream?: MediaStream;
  private chunks: Blob[] = [];
  private accumulatedMs = 0;
  private startedAt = 0;
  private timer?: number;
  private stopLevelMeter?: () => void;

  constructor(private readonly deps: RecorderDependencies) {}

  getSnapshot = (): RecorderSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(next: Partial<RecorderSnapshot>) {
    this.snapshot = { ...this.snapshot, ...next };
    this.listeners.forEach((listener) => listener());
  }

  private clearTimer() {
    if (this.timer !== undefined) this.deps.stopTimer(this.timer);
    this.timer = undefined;
  }

  private finishRunningPeriod() {
    if (this.snapshot.state === 'recording') this.accumulatedMs += this.deps.now() - this.startedAt;
    this.clearTimer();
  }

  private refreshElapsed = () => {
    const running = this.snapshot.state === 'recording' ? this.deps.now() - this.startedAt : 0;
    this.publish({ seconds: Math.floor((this.accumulatedMs + running) / 1000) });
  };

  start = async () => {
    this.releaseMedia();
    this.accumulatedMs = 0;
    this.chunks = [];
    this.publish({ ...INITIAL, state: 'requesting' });
    try {
      const stream = await this.deps.getUserMedia();
      this.stream = stream;
      const recorder = this.deps.createMediaRecorder(stream);
      this.mediaRecorder = recorder;
      recorder.ondataavailable = ({ data }) => {
        if (data.size > 0) this.chunks.push(data);
      };
      recorder.onerror = () => {
        this.finishRunningPeriod();
        this.releaseMedia();
        this.publish({ state: 'error', error: 'recorder', level: 0 });
      };
      recorder.onstop = () => {
        const type = this.chunks[0]?.type || preferredMimeType() || 'audio/webm';
        const audio = new Blob(this.chunks, { type });
        this.releaseMedia();
        this.publish({ state: 'saved', audio, error: null, level: 0 });
      };
      this.stopLevelMeter = this.deps.createLevelMeter(stream, (level) => this.publish({ level }));
      this.startedAt = this.deps.now();
      recorder.start(CHUNK_MS);
      this.publish({ state: 'recording' });
      this.timer = this.deps.startTimer(this.refreshElapsed);
    } catch (error) {
      this.releaseMedia();
      this.publish({ state: 'error', error: recorderError(error), level: 0 });
    }
  };

  pause = () => {
    if (this.snapshot.state !== 'recording') return;
    this.finishRunningPeriod();
    this.mediaRecorder?.pause();
    this.publish({ state: 'paused', level: 0, seconds: Math.floor(this.accumulatedMs / 1000) });
  };

  resume = () => {
    if (this.snapshot.state !== 'paused') return;
    this.mediaRecorder?.resume();
    this.startedAt = this.deps.now();
    this.publish({ state: 'recording' });
    this.timer = this.deps.startTimer(this.refreshElapsed);
  };

  stop = () => {
    if (this.snapshot.state !== 'recording' && this.snapshot.state !== 'paused') return;
    this.finishRunningPeriod();
    this.publish({ seconds: Math.floor(this.accumulatedMs / 1000) });
    this.mediaRecorder?.stop();
  };

  dispose = () => {
    this.clearTimer();
    this.releaseMedia();
  };

  private releaseMedia() {
    this.stopLevelMeter?.();
    this.stopLevelMeter = undefined;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = undefined;
  }
}
