import { useCallback, useEffect, useRef, useState } from 'react';

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'paused' | 'saved' | 'error';
export type RecorderError = 'permission' | 'noMicrophone' | 'recorder';

export interface RecorderSnapshot {
  state: RecorderState;
  seconds: number;
  audio: Blob | null;
  error: RecorderError | null;
  level: number;
}

export interface Recorder extends RecorderSnapshot {
  start: () => void | Promise<void>;
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

const TICK_MS = 500;

// Stand-in for the native foreground-service recorder (ADR-001). It only counts time: no microphone, no audio.
// Elapsed time comes from the wall clock, not from counting timer ticks, because Android throttles JS timers
// when the screen is off. The real recorder must follow the same rule.
export function useSimulatedRecorder(): Recorder {
  const [state, setState] = useState<RecorderState>('idle');
  const [seconds, setSeconds] = useState(0);
  const accumulatedMs = useRef(0);

  useEffect(() => {
    if (state !== 'recording') return;
    const startedAt = Date.now();
    const base = accumulatedMs.current;
    const timer = setInterval(() => {
      accumulatedMs.current = base + (Date.now() - startedAt);
      setSeconds(Math.floor(accumulatedMs.current / 1000));
    }, TICK_MS);
    return () => {
      accumulatedMs.current = base + (Date.now() - startedAt);
      clearInterval(timer);
    };
  }, [state]);

  const start = useCallback(() => {
    accumulatedMs.current = 0;
    setSeconds(0);
    setState('recording');
  }, []);
  const pause = useCallback(() => setState('paused'), []);
  const resume = useCallback(() => setState('recording'), []);
  const stop = useCallback(() => setState('saved'), []);

  return {
    state,
    seconds,
    audio: state === 'saved' ? new Blob([]) : null,
    error: null,
    level: 0,
    start,
    pause,
    resume,
    stop,
  };
}
