import { Cloud, DesktopTower } from '@/ui/icons';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useConnection } from '@/connection/ConnectionProvider';
import { normalizeAzureProfile } from '@/connection/resolveConnection';
import type { AzureProfile } from '@/connection/types';
import { space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';
import { TextField } from '@/ui/TextField';

const EMPTY_PROFILE: AzureProfile = { apiBaseUrl: '', issuer: '', clientId: '', apiScope: '' };

export default function FirstRunScreen() {
  const { t } = useTranslation();
  const { connectAzure, chooseDemo } = useConnection();
  const [profile, setProfile] = useState(EMPTY_PROFILE);
  const [probe, setProbe] = useState<'checking' | 'available' | 'unavailable'>(() =>
    typeof window === 'undefined' ? 'unavailable' : 'checking',
  );
  const [submitted, setSubmitted] = useState(false);
  const validProfile = useMemo(() => normalizeAzureProfile(profile), [profile]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const controller = new AbortController();
    fetch(`${window.location.origin}/healthz`, { signal: controller.signal })
      .then((response) => setProbe(response.ok ? 'available' : 'unavailable'))
      .catch(() => setProbe('unavailable'));
    return () => controller.abort();
  }, []);

  const update = (key: keyof AzureProfile, value: string) => setProfile((current) => ({ ...current, [key]: value }));

  return (
    <Screen title={t('connection.title')} intro={t('connection.intro')} wordmark bottomInset>
      <Surface>
        <View style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
          <DesktopTower size={24} weight="duotone" />
          <Text variant="title">{t('connection.local.title')}</Text>
        </View>
        <Text tone="muted">{t('connection.local.body')}</Text>
        {probe === 'unavailable' ? <Text tone="danger">{t('connection.local.unavailable')}</Text> : null}
        <Button
          label={probe === 'checking' ? t('connection.local.checking') : t('connection.local.open')}
          disabled={probe !== 'available'}
          onPress={() => {
            if (typeof window !== 'undefined') window.location.reload();
          }}
        />
      </Surface>

      <Surface>
        <View style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
          <Cloud size={24} weight="duotone" />
          <Text variant="title">{t('connection.azure.title')}</Text>
        </View>
        <Text tone="muted">{t('connection.azure.body')}</Text>
        <TextField
          label={t('connection.azure.api')}
          value={profile.apiBaseUrl}
          onChangeText={(value) => update('apiBaseUrl', value)}
          autoCapitalize="none"
          keyboardType="url"
        />
        <TextField
          label={t('connection.azure.issuer')}
          value={profile.issuer}
          onChangeText={(value) => update('issuer', value)}
          autoCapitalize="none"
          keyboardType="url"
        />
        <TextField
          label={t('connection.azure.clientId')}
          value={profile.clientId}
          onChangeText={(value) => update('clientId', value)}
          autoCapitalize="none"
        />
        <TextField
          label={t('connection.azure.scope')}
          value={profile.apiScope}
          onChangeText={(value) => update('apiScope', value)}
          autoCapitalize="none"
        />
        {submitted && !validProfile ? <Text tone="danger">{t('connection.azure.validation')}</Text> : null}
        <Button
          label={t('connection.azure.signIn')}
          onPress={() => {
            setSubmitted(true);
            if (validProfile) connectAzure(validProfile);
          }}
        />
      </Surface>
      <Button variant="ghost" label={t('connection.demo')} onPress={chooseDemo} />
    </Screen>
  );
}
