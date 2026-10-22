import { useRouter } from 'expo-router';
import { Check, Info, Microphone, Pause, Play, Stop, Warning } from 'phosphor-react-native';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { formatClock } from '@/i18n/format';
import { usePrefs, type RetentionDays } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { fonts, space, touch } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Segmented } from '@/ui/Segmented';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

import { LevelBars } from './LevelBars';
import { useSimulatedRecorder } from './useSimulatedRecorder';

const RETENTION_OPTIONS: RetentionDays[] = [7, 30, 90];
const PIPELINE = ['upload', 'transcribe', 'extract', 'review', 'ready'] as const;

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

      <Button label={t('capture.start')} icon={Microphone} disabled={!attested} onPress={onStart} fullWidth />
    </>
  );
}

function Recording({ recorder }: { recorder: ReturnType<typeof useSimulatedRecorder> }) {
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
      <LevelBars active={!paused} />
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

function Saved({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  return (
    <>
      <Surface tone="accent">
        <Text variant="title">{t('capture.saved.title')}</Text>
        <Text tone="muted">{t('capture.saved.body')}</Text>
      </Surface>
      <View style={{ gap: space[3] }}>
        {PIPELINE.map((step, index) => (
          <View key={step} style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
            <Text variant="mono" tone="muted" style={{ width: 20 }}>
              {index + 1}
            </Text>
            <Text>{t(`capture.steps.${step}`)}</Text>
          </View>
        ))}
      </View>
      <Button label={t('common.done')} onPress={onDone} fullWidth />
    </>
  );
}

export default function CaptureScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const recorder = useSimulatedRecorder();

  return (
    <Screen back title={t('capture.title')} bottomInset>
      <SimulationNotice />
      {recorder.state === 'idle' ? <Attestation onStart={recorder.start} /> : null}
      {recorder.state === 'recording' || recorder.state === 'paused' ? <Recording recorder={recorder} /> : null}
      {recorder.state === 'saved' ? <Saved onDone={() => router.replace('/')} /> : null}
    </Screen>
  );
}
