import { Check } from '@/ui/icons';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { TransientSignInButton } from '@/auth/TransientSignInButton';
import type { LocalEngineApi, SyncProgress } from '@/connection/localApi';
import {
  AZURE_TARGET_KEY,
  browserEnvironment,
  normalizeAzureProfile,
  readStoredProfile,
  safeSet,
} from '@/connection/resolveConnection';
import type { AzureProfile } from '@/connection/types';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { SectionHeader } from '@/ui/SectionHeader';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';
import { TextField } from '@/ui/TextField';

const EMPTY: AzureProfile = { apiBaseUrl: '', issuer: '', clientId: '', apiScope: '' };

export function AzureSyncSection({ api }: { api: LocalEngineApi }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const environment = useMemo(() => browserEnvironment(), []);
  const [target, setTarget] = useState<AzureProfile>(
    () => readStoredProfile(environment?.localStorage, AZURE_TARGET_KEY) ?? EMPTY,
  );
  const [accessToken, setAccessToken] = useState<string>();
  const [force, setForce] = useState(false);
  const [progress, setProgress] = useState<SyncProgress>();
  const [failed, setFailed] = useState(false);
  const profile = useMemo(() => normalizeAzureProfile(target), [target]);
  const update = (key: keyof AzureProfile, value: string) => setTarget((current) => ({ ...current, [key]: value }));
  const rememberToken = useCallback(
    (token: string) => {
      if (!profile) return;
      safeSet(environment?.localStorage, AZURE_TARGET_KEY, JSON.stringify(profile));
      setAccessToken(token);
    },
    [environment, profile],
  );

  useEffect(() => {
    if (progress?.state !== 'running') return;
    let active = true;
    const poll = () => {
      void api.getSync().then(
        (next) => active && setProgress(next),
        () => active && setFailed(true),
      );
    };
    const timer = setInterval(poll, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, progress?.state]);

  const copy = () => {
    if (!profile || !accessToken) return;
    setFailed(false);
    void api.startSync({ cloudUrl: profile.apiBaseUrl, accessToken, force }).then(setProgress, () => setFailed(true));
  };

  return (
    <View style={{ gap: space[3] }}>
      <SectionHeader title={t('settings.sync.title')} />
      <Surface>
        <Text tone="muted">{t('settings.sync.body')}</Text>
        <TextField
          label={t('connection.azure.api')}
          value={target.apiBaseUrl}
          onChangeText={(v) => update('apiBaseUrl', v)}
          autoCapitalize="none"
        />
        <TextField
          label={t('connection.azure.issuer')}
          value={target.issuer}
          onChangeText={(v) => update('issuer', v)}
          autoCapitalize="none"
        />
        <TextField
          label={t('connection.azure.clientId')}
          value={target.clientId}
          onChangeText={(v) => update('clientId', v)}
          autoCapitalize="none"
        />
        <TextField
          label={t('connection.azure.scope')}
          value={target.apiScope}
          onChangeText={(v) => update('apiScope', v)}
          autoCapitalize="none"
        />
        {profile ? (
          <TransientSignInButton profile={profile} onToken={rememberToken} />
        ) : (
          <Button variant="secondary" label={t('settings.sync.signIn')} disabled onPress={() => undefined} />
        )}
        {accessToken ? <Text tone="accent">{t('settings.sync.signedIn')}</Text> : null}
        <Pressable
          accessibilityRole="checkbox"
          accessibilityState={{ checked: force }}
          accessibilityLabel={t('settings.sync.force')}
          onPress={() => setForce((value) => !value)}
          style={{ minHeight: touch, flexDirection: 'row', alignItems: 'center', gap: space[3] }}
        >
          <View
            style={{
              width: 24,
              height: 24,
              borderRadius: radius.control,
              borderWidth: 2,
              borderColor: force ? colors.accent : colors.borderStrong,
              backgroundColor: force ? colors.accent : 'transparent',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {force ? <Check size={16} color={colors.accentText} weight="bold" /> : null}
          </View>
          <Text>{t('settings.sync.force')}</Text>
        </Pressable>
        <Button
          label={t('settings.sync.copy')}
          disabled={!accessToken || progress?.state === 'running'}
          onPress={copy}
        />
        {failed ? <Text tone="danger">{t('settings.sync.error')}</Text> : null}
        {progress ? (
          <View style={{ gap: space[2] }} accessibilityLiveRegion="polite">
            <Text variant="heading">{t(`settings.sync.state.${progress.state}`)}</Text>
            <Text>{t('settings.sync.counts.total', { count: progress.total })}</Text>
            <Text>{t('settings.sync.counts.copied', { count: progress.copied })}</Text>
            <Text>{t('settings.sync.counts.unchanged', { count: progress.unchanged })}</Text>
            <Text>{t('settings.sync.counts.deleted', { count: progress.deleted })}</Text>
            {progress.failed.map((item) => (
              <Text key={`${item.meetingId}-${item.reason}`} tone="danger">
                {t('settings.sync.failedMeeting', { id: item.meetingId, reason: item.reason })}
              </Text>
            ))}
          </View>
        ) : null}
      </Surface>
    </View>
  );
}
