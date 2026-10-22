import { useCallback, useEffect, useState } from 'react';

export type RecorderState = 'idle' | 'recording' | 'paused' | 'saved';

export interface Recorder {
  state: RecorderState;
  seconds: number;
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

// Stand-in for the native foreground-service recorder (ADR-001). It only counts time: no microphone, no audio.
export function useSimulatedRecorder(): Recorder {
  const [state, setState] = useState<RecorderState>('idle');
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (state !== 'recording') return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [state]);

  const start = useCallback(() => {
    setSeconds(0);
    setState('recording');
  }, []);
  const pause = useCallback(() => setState('paused'), []);
  const resume = useCallback(() => setState('recording'), []);
  const stop = useCallback(() => setState('saved'), []);

  return { state, seconds, start, pause, resume, stop };
}
