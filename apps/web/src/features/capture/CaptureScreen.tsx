'use client';

import { Check, Microphone, Pause, Play, Stop, Warning } from '@phosphor-icons/react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, Checkbox, Progress, Segmented, Surface } from '@/components/ui/primitives';
import type { MeetingState, RetentionDays } from '@/lib/domain/types';
import { formatClock } from '@/lib/i18n/format';
import { useMeetingState } from '@/lib/queries';
import { useRepository, useVoiceApi } from '@/providers/data';
import { usePrefs } from '@/providers/prefs';
import styles from '../features.module.css';
import { meetingHref } from '../meetings/MeetingRow';
import { LevelBars } from './LevelBars';
import { BrowserPcmCapture } from './pcm';
import { BrowserRecorder, browserDependencies, canRecord } from './recorder';
import { VoiceSessionClient, type VoiceEvent } from './voiceClient';

const RETENTION_OPTIONS: RetentionDays[] = [7, 30, 90];
const PIPELINE = ['upload', 'transcribe', 'extract', 'review', 'ready'] as const;
const STEP_OF_STATE: Record<MeetingState, (typeof PIPELINE)[number] | null> = {
  uploading: 'upload',
  transcribing: 'transcribe',
  extracting: 'extract',
  review: 'review',
  ready: 'ready',
  failed: null,
};

function Attestation({ onStart }: { onStart(): void }) {
  const { t } = useTranslation();
  const { retentionDays, setRetentionDays } = usePrefs();
  const [attested, setAttested] = useState(false);
  return (
    <>
      <Surface tone="warning" as="section" aria-labelledby="notice-title">
        <div className={styles.row}>
          <Warning size={20} weight="fill" className={styles.warningText} aria-hidden />
          <h2 id="notice-title" className={styles.warningText}>
            {t('capture.notice.title')}
          </h2>
        </div>
        <p>{t('capture.notice.body')}</p>
      </Surface>
      <Checkbox
        checked={attested}
        onChange={setAttested}
        label={t('capture.attest')}
        note={t('capture.attestNote')}
      />
      <div className={styles.stack}>
        <p className={styles.label}>{t('capture.retentionLabel')}</p>
        <Segmented
          label={t('capture.retentionLabel')}
          value={String(retentionDays)}
          onChange={(value) => setRetentionDays(Number(value) as RetentionDays)}
          options={RETENTION_OPTIONS.map((days) => ({
            value: String(days),
            label: t('capture.retentionOption', { count: days }),
          }))}
        />
      </div>
      <div className={styles.stack}>
        <Button icon={Microphone} disabled={!attested} onClick={onStart} fullWidth>
          {t('capture.start')}
        </Button>
        {attested ? null : <p className={`${styles.muted} ${styles.small}`}>{t('capture.startHint')}</p>}
      </div>
    </>
  );
}

type Pipeline = 'uploading' | MeetingState | 'error';

function Processing({
  state,
  progress,
  meetingId,
  onRetry,
}: {
  state: Pipeline;
  progress: number;
  meetingId?: string;
  onRetry(): void;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const failed = state === 'error' || state === 'failed';
  const current = failed
    ? -1
    : PIPELINE.indexOf(STEP_OF_STATE[state === 'uploading' ? 'uploading' : state] ?? 'upload');
  const canOpen = (state === 'review' || state === 'ready') && !!meetingId;
  return (
    <>
      <Surface tone={failed ? 'warning' : 'accent'} as="section" aria-live="polite">
        <h2>{t(failed ? 'capture.processing.failedTitle' : 'capture.saved.title')}</h2>
        <p className={styles.muted}>{t(failed ? 'capture.processing.failedBody' : 'capture.saved.body')}</p>
        {state === 'uploading' ? (
          <div className={styles.stack}>
            <Progress value={progress} label={t('capture.steps.upload')} />
            <span className={`${styles.muted} ${styles.small}`}>
              {t('capture.processing.uploadProgress', { percent: Math.round(progress * 100) })}
            </span>
          </div>
        ) : null}
      </Surface>
      <ol className={styles.steps}>
        {PIPELINE.map((step, index) => (
          <li key={step} aria-current={index === current ? 'step' : undefined}>
            {index < current ? (
              <Check size={18} weight="bold" className={styles.accentText} aria-hidden />
            ) : (
              <span className={`mono ${index === current ? styles.accentText : styles.muted}`}>
                {index + 1}
              </span>
            )}
            <span>{t(`capture.steps.${step}`)}</span>
          </li>
        ))}
      </ol>
      {failed ? (
        <Button onClick={onRetry} fullWidth>
          {t('common.retry')}
        </Button>
      ) : null}
      {canOpen ? (
        <Button onClick={() => router.replace(meetingHref(meetingId))} fullWidth>
          {t('capture.processing.openMeeting')}
        </Button>
      ) : null}
    </>
  );
}

export function CaptureScreen() {
  const { t } = useTranslation();
  const repository = useRepository();
  const voice = useVoiceApi();
  const { locale, retentionDays } = usePrefs();
  const recorder = useMemo(() => new BrowserRecorder(browserDependencies()), []);
  const snapshot = useSyncExternalStore(recorder.subscribe, recorder.getSnapshot, recorder.getSnapshot);
  const supported = useSyncExternalStore(
    () => () => undefined,
    canRecord,
    () => true,
  );
  const [meetingId, setMeetingId] = useState<string>();
  const [uploaded, setUploaded] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [liveState, setLiveState] = useState<'idle' | 'starting' | 'recording' | 'reconnecting' | 'saved'>(
    'idle',
  );
  const [liveSeconds, setLiveSeconds] = useState(0);
  const [liveLevel, setLiveLevel] = useState(0);
  const [liveTranscript, setLiveTranscript] = useState<string[]>([]);
  const liveClient = useRef<VoiceSessionClient | undefined>(undefined);
  const liveCapture = useRef<BrowserPcmCapture | undefined>(undefined);
  const liveStartedAt = useRef(0);
  const started = useRef(false);
  const remote = useMeetingState(uploaded ? meetingId : undefined);

  useEffect(
    () => () => {
      recorder.dispose();
      liveClient.current?.dispose();
      void liveCapture.current?.stop();
    },
    [recorder],
  );

  useEffect(() => {
    if (liveState !== 'recording' && liveState !== 'reconnecting') return;
    const timer = window.setInterval(
      () => setLiveSeconds(Math.floor((Date.now() - liveStartedAt.current) / 1_000)),
      250,
    );
    return () => window.clearInterval(timer);
  }, [liveState]);

  const process = async (audio: Blob, existing?: string) => {
    setFailed(false);
    setUploaded(false);
    setProgress(0);
    try {
      const id = existing ?? (await repository.createMeeting({ locale, retentionDays })).id;
      setMeetingId(id);
      await repository.uploadAudio(id, audio, setProgress);
      setUploaded(true);
    } catch {
      setFailed(true);
    }
  };

  const start = async () => {
    setLiveState('starting');
    setLiveTranscript([]);
    setLiveSeconds(0);
    let createdMeeting: string | undefined;
    try {
      createdMeeting = (await repository.createMeeting({ locale, retentionDays })).id;
      setMeetingId(createdMeeting);
      const session = await voice.openMeeting(createdMeeting, locale);
      const capture = new BrowserPcmCapture();
      const onEvent = (event: VoiceEvent) => {
        if (event.type === 'transcript.final' && typeof event.text === 'string') {
          setLiveTranscript((current) => [...current.slice(-4), event.text as string]);
        }
      };
      const client = new VoiceSessionClient(session, onEvent, {
        refreshTicket: voice.refreshTicket,
        onStatus: (status) => {
          if (status === 'connected') setLiveState('recording');
          if (status === 'reconnecting') setLiveState('reconnecting');
          if (status === 'closed') {
            void capture.stop();
            setLiveLevel(0);
            setUploaded(true);
            setLiveState('saved');
          }
        },
      });
      liveClient.current = client;
      liveCapture.current = capture;
      await capture.start(
        ({ offsetMs, pcm }) => client.sendPcm(offsetMs, pcm),
        (level) => setLiveLevel(level),
      );
      liveStartedAt.current = Date.now();
      client.connect();
    } catch {
      liveClient.current?.dispose();
      await liveCapture.current?.stop();
      liveClient.current = undefined;
      liveCapture.current = undefined;
      if (createdMeeting) await repository.deleteMeeting(createdMeeting).catch(() => undefined);
      setMeetingId(undefined);
      setLiveState('idle');
      await recorder.start(); // gateway disabled: retain the existing encrypted-upload path
    }
  };

  const stopLive = async () => {
    setLiveState('saved');
    setLiveLevel(0);
    await liveCapture.current?.stop();
    liveClient.current?.end();
    liveCapture.current = undefined;
    setUploaded(true);
  };

  useEffect(() => {
    if (snapshot.state === 'saved' && snapshot.audio && !started.current) {
      started.current = true;
      void process(snapshot.audio);
    }
    // `process` closes over values that must not restart an upload already under way.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.state, snapshot.audio]);

  const pipeline: Pipeline = failed ? 'error' : uploaded ? (remote.data ?? 'transcribing') : 'uploading';
  const settledFailure = remote.data === 'failed';

  const announcement =
    liveState === 'recording' || liveState === 'reconnecting' || snapshot.state === 'recording'
      ? t('capture.state.recording')
      : snapshot.state === 'paused'
        ? t('capture.state.paused')
        : snapshot.state === 'requesting'
          ? t('capture.state.requesting')
          : '';

  return (
    <>
      <PageHeader title={t('capture.title')} backHref="/" />
      <p className="visually-hidden" role="status">
        {announcement}
      </p>

      {!supported ? (
        <Surface tone="warning">
          <h2 className={styles.warningText}>{t('capture.errors.title')}</h2>
          <p>{t('capture.errors.noMicrophone')}</p>
        </Surface>
      ) : null}
      {supported && snapshot.state === 'idle' && liveState === 'idle' ? (
        <Attestation onStart={() => void start()} />
      ) : null}
      {snapshot.state === 'requesting' || liveState === 'starting' ? (
        <p className={styles.muted}>{t('capture.state.requesting')}</p>
      ) : null}
      {snapshot.state === 'error' ? (
        <Surface tone="warning">
          <h2 className={styles.warningText}>{t('capture.errors.title')}</h2>
          <p>{t(`capture.errors.${snapshot.error ?? 'recorder'}`)}</p>
          <div>
            <Button onClick={() => void recorder.start()}>{t('common.retry')}</Button>
          </div>
        </Surface>
      ) : null}
      {snapshot.state === 'recording' || snapshot.state === 'paused' ? (
        <div className={styles.center}>
          <span className={snapshot.state === 'paused' ? styles.muted : styles.accentText}>
            {t(snapshot.state === 'paused' ? 'capture.state.paused' : 'capture.state.recording')}
          </span>
          <span className={styles.timer} role="timer">
            {formatClock(snapshot.seconds)}
          </span>
          <LevelBars active={snapshot.state === 'recording'} level={snapshot.level} />
          <div className={styles.row}>
            <Button
              variant="secondary"
              icon={snapshot.state === 'paused' ? Play : Pause}
              onClick={snapshot.state === 'paused' ? recorder.resume : recorder.pause}
            >
              {t(snapshot.state === 'paused' ? 'capture.resume' : 'capture.pause')}
            </Button>
            <Button icon={Stop} onClick={recorder.stop}>
              {t('capture.stop')}
            </Button>
          </div>
        </div>
      ) : null}
      {liveState === 'recording' || liveState === 'reconnecting' ? (
        <div className={styles.center}>
          <span className={liveState === 'reconnecting' ? styles.warningText : styles.accentText}>
            {t('capture.state.recording')}
          </span>
          <span className={styles.timer} role="timer">
            {formatClock(liveSeconds)}
          </span>
          <LevelBars active={liveState === 'recording'} level={liveLevel} />
          {liveTranscript.length ? (
            <Surface as="section" aria-live="polite">
              <h2>{t('capture.steps.transcribe')}</h2>
              {liveTranscript.map((line, index) => (
                <p key={`${index}-${line}`}>{line}</p>
              ))}
            </Surface>
          ) : null}
          <Button icon={Stop} onClick={() => void stopLive()}>
            {t('capture.stop')}
          </Button>
        </div>
      ) : null}
      {snapshot.state === 'saved' || liveState === 'saved' ? (
        <Processing
          state={settledFailure ? 'failed' : pipeline}
          progress={progress}
          meetingId={meetingId}
          onRetry={() => snapshot.audio && void process(snapshot.audio, meetingId)}
        />
      ) : null}
    </>
  );
}
