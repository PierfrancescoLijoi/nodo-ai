import { useEffect, useState } from 'react';

import type { Recorder, RecorderError, RecorderSnapshot } from './useSimulatedRecorder';

export interface MediaRecorderLike {
  state: string;
  ondataavailable: ((event: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: (() => void) | null;
  start(timeslice?: number): void;
  pause(): void;
  resume(): void;
  stop(): void;
}

interface BrowserRecorderDependencies {
  now(): number;
  getUserMedia(): Promise<MediaStream>;
  createMediaRecorder(stream: MediaStream): MediaRecorderLike;
  startTimer(callback: () => void): number;
  stopTimer(id: number): void;
  createLevelMeter(stream: MediaStream, onLevel: (level: number) => void): () => void;
}

type Listener = () => void;

function recorderError(error: unknown): RecorderError {
  const name = error instanceof Error ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'permission';
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'noMicrophone';
  return 'recorder';
}

export function preferredMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((type) =>
    MediaRecorder.isTypeSupported(type),
  );
}

function levelMeter(stream: MediaStream, onLevel: (level: number) => void): () => void {
  const AudioContextClass = window.AudioContext;
  const context = new AudioContextClass();
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

function defaultDependencies(): BrowserRecorderDependencies {
  return {
    now: Date.now,
    getUserMedia: async () => {
      if (!navigator.mediaDevices?.getUserMedia)
        throw Object.assign(new Error('Microphone unavailable'), { name: 'NotFoundError' });
      return navigator.mediaDevices.getUserMedia({ audio: true });
    },
    createMediaRecorder: (stream) => {
      const mimeType = preferredMimeType();
      return new MediaRecorder(stream, mimeType ? { mimeType } : undefined) as unknown as MediaRecorderLike;
    },
    startTimer: (callback) => window.setInterval(callback, 250),
    stopTimer: (id) => window.clearInterval(id),
    createLevelMeter: levelMeter,
  };
}

export class BrowserRecorder {
  private snapshot: RecorderSnapshot = { state: 'idle', seconds: 0, audio: null, error: null, level: 0 };
  private readonly listeners = new Set<Listener>();
  private mediaRecorder?: MediaRecorderLike;
  private stream?: MediaStream;
  private chunks: Blob[] = [];
  private accumulatedMs = 0;
  private startedAt = 0;
  private timer?: number;
  private stopLevelMeter?: () => void;

  constructor(private readonly dependencies: BrowserRecorderDependencies = defaultDependencies()) {}

  getSnapshot = (): RecorderSnapshot => this.snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(next: Partial<RecorderSnapshot>) {
    this.snapshot = { ...this.snapshot, ...next };
    this.listeners.forEach((listener) => listener());
  }

  private clearTimer() {
    if (this.timer !== undefined) this.dependencies.stopTimer(this.timer);
    this.timer = undefined;
  }

  private finishRunningPeriod() {
    if (this.snapshot.state === 'recording') this.accumulatedMs += this.dependencies.now() - this.startedAt;
    this.clearTimer();
  }

  refreshElapsed = () => {
    const running = this.snapshot.state === 'recording' ? this.dependencies.now() - this.startedAt : 0;
    this.publish({ seconds: Math.floor((this.accumulatedMs + running) / 1000) });
  };

  start = async () => {
    this.disposeMedia();
    this.accumulatedMs = 0;
    this.chunks = [];
    this.publish({ state: 'requesting', seconds: 0, audio: null, error: null, level: 0 });
    try {
      const stream = await this.dependencies.getUserMedia();
      this.stream = stream;
      const recorder = this.dependencies.createMediaRecorder(stream);
      this.mediaRecorder = recorder;
      recorder.ondataavailable = ({ data }) => {
        if (data.size > 0) this.chunks.push(data);
      };
      recorder.onerror = () => {
        this.finishRunningPeriod();
        this.disposeMedia();
        this.publish({ state: 'error', error: 'recorder', level: 0 });
      };
      recorder.onstop = () => {
        const type = this.chunks[0]?.type || preferredMimeType() || 'audio/webm';
        const audio = new Blob(this.chunks, { type });
        this.disposeMedia();
        this.publish({ state: 'saved', audio, error: null, level: 0 });
      };
      this.stopLevelMeter = this.dependencies.createLevelMeter(stream, (level) => this.publish({ level }));
      this.startedAt = this.dependencies.now();
      recorder.start(1000);
      this.publish({ state: 'recording' });
      this.timer = this.dependencies.startTimer(this.refreshElapsed);
    } catch (error) {
      this.disposeMedia();
      this.publish({ state: 'error', error: recorderError(error), level: 0 });
    }
  };

  pause = () => {
    if (this.snapshot.state !== 'recording') return;
    this.finishRunningPeriod();
    this.mediaRecorder?.pause();
    this.refreshElapsed();
    this.publish({ state: 'paused', level: 0 });
  };

  resume = () => {
    if (this.snapshot.state !== 'paused') return;
    this.mediaRecorder?.resume();
    this.startedAt = this.dependencies.now();
    this.publish({ state: 'recording' });
    this.timer = this.dependencies.startTimer(this.refreshElapsed);
  };

  stop = () => {
    if (this.snapshot.state !== 'recording' && this.snapshot.state !== 'paused') return;
    this.finishRunningPeriod();
    this.refreshElapsed();
    this.mediaRecorder?.stop();
  };

  dispose = () => {
    this.clearTimer();
    this.disposeMedia();
  };

  private disposeMedia() {
    this.stopLevelMeter?.();
    this.stopLevelMeter = undefined;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = undefined;
  }
}

export function useWebRecorder(): Recorder {
  const [recorder] = useState(() => new BrowserRecorder());
  const [snapshot, setSnapshot] = useState(recorder.getSnapshot);
  useEffect(() => recorder.subscribe(() => setSnapshot(recorder.getSnapshot())), [recorder]);
  useEffect(() => () => recorder.dispose(), [recorder]);
  return {
    ...snapshot,
    start: recorder.start,
    pause: recorder.pause,
    resume: recorder.resume,
    stop: recorder.stop,
  };
}
