import { useRouter } from 'expo-router';
import { Check, Info, Microphone, Pause, Play, Stop, Warning } from '@/ui/icons';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, Pressable, View } from 'react-native';

import { formatClock } from '@/i18n/format';
import { useConnection } from '@/connection/ConnectionProvider';
import { useRepository } from '@/data/RepositoryProvider';
import type { MeetingState } from '@/data/types';
import { usePrefs, type RetentionDays } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { fonts, space, touch } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Segmented } from '@/ui/Segmented';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

import { LevelBars } from './LevelBars';
import { useSimulatedRecorder, type Recorder } from './useSimulatedRecorder';
import { useWebRecorder } from './webRecorder';

const RETENTION_OPTIONS: RetentionDays[] = [7, 30, 90];
const PIPELINE = ['upload', 'transcribe', 'extract', 'review', 'ready'] as const;
const STEP_OF_STATE = {
  uploading: 'upload',
  transcribing: 'transcribe',
  extracting: 'extract',
  review: 'review',
  ready: 'ready',
} as const;

function SimulationNotice() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  return (
    <Surface>
      <View style={{ flexDirection: 'row', gap: space[3], alignItems: 'flex-start' }}>
        <Info size={20} color={colors.accent} weight="fill" style={{ marginTop: 1 }} />
        <Text variant="small" tone="muted" style={{ flex: 1 }}>
          {t('capture.simulation')}
        </Text>
      </View>
    </Surface>
  );
}

function Attestation({ onStart }: { onStart: () => void }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { retentionDays, setRetentionDays } = usePrefs();
  const [attested, setAttested] = useState(false);

  return (
    <>
      <Surface tone="warning">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[2] }}>
          <Warning size={20} color={colors.warning} weight="fill" />
          <Text variant="heading" tone="warning" accessibilityRole="header">
            {t('capture.notice.title')}
          </Text>
        </View>
        <Text>{t('capture.notice.body')}</Text>
      </Surface>

      <Pressable
        accessibilityRole="checkbox"
        accessibilityState={{ checked: attested }}
        onPress={() => setAttested((v) => !v)}
        style={{ flexDirection: 'row', gap: space[3], alignItems: 'flex-start', minHeight: touch }}
      >
        <View
          style={{
            width: 24,
            height: 24,
            marginTop: 1,
            borderRadius: 6,
            borderWidth: 2,
            borderColor: attested ? colors.accent : colors.textMuted,
            backgroundColor: attested ? colors.accent : 'transparent',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {attested ? <Check size={16} color={colors.accentText} weight="bold" /> : null}
        </View>
        <View style={{ flex: 1, gap: space[1] }}>
          <Text>{t('capture.attest')}</Text>
          <Text variant="small" tone="muted">
            {t('capture.attestNote')}
          </Text>
        </View>
      </Pressable>

      <View style={{ gap: space[2] }}>
        <Text variant="label">{t('capture.retentionLabel')}</Text>
        <Segmented
          label={t('capture.retentionLabel')}
          value={String(retentionDays)}
          onChange={(value) => setRetentionDays(Number(value) as RetentionDays)}
          options={RETENTION_OPTIONS.map((days) => ({
            value: String(days),
            label: t('capture.retentionOption', { count: days }),
          }))}
        />
      </View>

      <Button
        label={t('capture.start')}
        hint={attested ? undefined : t('capture.startHint')}
        icon={Microphone}
        disabled={!attested}
        onPress={onStart}
        fullWidth
      />
    </>
  );
}

function Recording({ recorder, real }: { recorder: Recorder; real: boolean }) {
  const { t } = useTranslation();
  const paused = recorder.state === 'paused';
  return (
    <View style={{ gap: space[6], alignItems: 'center' }}>
      <Text variant="label" tone={paused ? 'muted' : 'accent'} accessibilityLiveRegion="polite">
        {t(paused ? 'capture.state.paused' : 'capture.state.recording')}
      </Text>
      <Text
        accessibilityRole="timer"
        style={{ fontFamily: fonts.mono, fontSize: 56, lineHeight: 62, letterSpacing: -2 }}
      >
        {formatClock(recorder.seconds)}
      </Text>
      <LevelBars active={!paused} level={real ? recorder.level : undefined} />
      <View style={{ flexDirection: 'row', gap: space[3], alignSelf: 'stretch' }}>
        <View style={{ flex: 1 }}>
          <Button
            variant="secondary"
            fullWidth
            icon={paused ? Play : Pause}
            label={t(paused ? 'capture.resume' : 'capture.pause')}
            onPress={paused ? recorder.resume : recorder.pause}
          />
        </View>
        <View style={{ flex: 1 }}>
          <Button fullWidth icon={Stop} label={t('capture.stop')} onPress={recorder.stop} />
        </View>
      </View>
    </View>
  );
}

type PipelineState = 'uploading' | MeetingState | 'error';

function Processing({
  state,
  progress,
  meetingId,
  onRetry,
  onOpen,
}: {
  state: PipelineState;
  progress: number;
  meetingId?: string;
  onRetry(): void;
  onOpen(): void;
}) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const current = state === 'error' || state === 'failed' ? -1 : PIPELINE.indexOf(STEP_OF_STATE[state]);
  const canOpen = state === 'review' || state === 'ready';
  return (
    <>
      <Surface tone={state === 'error' || state === 'failed' ? undefined : 'accent'}>
        <Text variant="title">
          {t(state === 'error' || state === 'failed' ? 'capture.processing.failedTitle' : 'capture.saved.title')}
        </Text>
        <Text tone="muted">
          {t(state === 'error' || state === 'failed' ? 'capture.processing.failedBody' : 'capture.saved.body')}
        </Text>
        {state === 'uploading' ? (
          <View style={{ gap: space[2] }}>
            <View style={{ height: 6, backgroundColor: colors.border, borderRadius: 999, overflow: 'hidden' }}>
              <View
                style={{ width: `${Math.round(progress * 100)}%`, height: '100%', backgroundColor: colors.accent }}
              />
            </View>
            <Text variant="small" tone="muted">
              {t('capture.processing.uploadProgress', { percent: Math.round(progress * 100) })}
            </Text>
          </View>
        ) : null}
      </Surface>
      <View style={{ gap: space[3] }}>
        {PIPELINE.map((step, index) => (
          <View key={step} style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
            <View style={{ width: 20 }}>
              {index < current ? (
                <Check size={18} color={colors.accent} weight="bold" />
              ) : (
                <Text variant="mono" tone={index === current ? 'accent' : 'muted'}>
                  {index + 1}
                </Text>
              )}
            </View>
            <Text>{t(`capture.steps.${step}`)}</Text>
          </View>
        ))}
      </View>
      {state === 'error' || state === 'failed' ? (
        <Button label={t('common.retry')} onPress={onRetry} fullWidth />
      ) : null}
      {canOpen && meetingId ? <Button label={t('capture.processing.openMeeting')} onPress={onOpen} fullWidth /> : null}
    </>
  );
}

export default function CaptureScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const repository = useRepository();
  const { locale, retentionDays } = usePrefs();
  const { connection } = useConnection();
  const simulated = useSimulatedRecorder();
  const web = useWebRecorder();
  const real = connection.mode !== 'mock' && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  const recorder = real ? web : simulated;
  const [pipeline, setPipeline] = useState<PipelineState>('uploading');
  const [progress, setProgress] = useState(0);
  const [meetingId, setMeetingId] = useState<string>();

  const processAudio = useCallback(
    async (retry = false) => {
      if (!recorder.audio) return;
      await Promise.resolve(); // state is set after the effect that started this has finished
      setPipeline('uploading');
      setProgress(0);
      try {
        const id = retry && meetingId ? meetingId : (await repository.createMeeting({ locale, retentionDays })).id;
        setMeetingId(id);
        await repository.uploadAudio(id, recorder.audio, setProgress);
        for (;;) {
          const state = await repository.getMeetingState(id);
          setPipeline(state);
          if (state === 'ready' || state === 'review' || state === 'failed') break;
          await new Promise<void>((resolve) => setTimeout(resolve, 3000));
        }
      } catch {
        setPipeline('error');
      }
    },
    [locale, meetingId, recorder.audio, repository, retentionDays],
  );

  useEffect(() => {
    if (recorder.state === 'saved' && recorder.audio && !meetingId) queueMicrotask(() => void processAudio());
  }, [meetingId, processAudio, recorder.audio, recorder.state]);

  // The focused Start button disappears when recording starts, so announce each state change.
  useEffect(() => {
    const message = {
      idle: undefined,
      recording: t('capture.state.recording'),
      paused: t('capture.state.paused'),
      saved: t('capture.saved.title'),
      requesting: t('capture.state.requesting'),
      error: recorder.error ? t(`capture.errors.${recorder.error}`) : t('capture.errors.recorder'),
    }[recorder.state];
    if (message) AccessibilityInfo.announceForAccessibility(message);
  }, [recorder.error, recorder.state, t]);

  return (
    <Screen back title={t('capture.title')} bottomInset>
      {!real ? <SimulationNotice /> : null}
      {recorder.state === 'idle' ? <Attestation onStart={recorder.start} /> : null}
      {recorder.state === 'requesting' ? <Text tone="muted">{t('capture.state.requesting')}</Text> : null}
      {recorder.state === 'error' ? (
        <Surface tone="warning">
          <Text variant="heading" tone="warning">
            {t('capture.errors.title')}
          </Text>
          <Text>{t(`capture.errors.${recorder.error ?? 'recorder'}`)}</Text>
          <Button label={t('common.retry')} onPress={() => void recorder.start()} />
        </Surface>
      ) : null}
      {recorder.state === 'recording' || recorder.state === 'paused' ? (
        <Recording recorder={recorder} real={real} />
      ) : null}
      {recorder.state === 'saved' ? (
        <Processing
          state={pipeline}
          progress={progress}
          meetingId={meetingId}
          onRetry={() => void processAudio(true)}
          onOpen={() => router.replace(`/meeting/${meetingId}`)}
        />
      ) : null}
    </Screen>
  );
}
