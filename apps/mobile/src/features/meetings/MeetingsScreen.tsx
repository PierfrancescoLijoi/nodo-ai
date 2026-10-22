import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useAsync } from '@/data/RepositoryProvider';
import { usePrefs } from '@/prefs/PrefsProvider';
import { space } from '@/theme/tokens';
import { MeetingRow } from '@/ui/MeetingRow';
import { Screen } from '@/ui/Screen';
import { SettingsButton } from '@/ui/SettingsButton';
import { Skeleton } from '@/ui/Skeleton';
import { Text } from '@/ui/Text';
import { TextField } from '@/ui/TextField';

export default function MeetingsScreen() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const meetings = useAsync((r) => r.listMeetings());
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    if (meetings.status !== 'ready') return [];
    const needle = query.trim().toLowerCase();
    return meetings.data.filter((m) =>
      [m.title, m.tags.join(' '), m.participants.join(' ')].join(' ').toLowerCase().includes(needle),
    );
  }, [meetings, query]);

  return (
    <Screen title={t('meetings.title')} right={<SettingsButton />}>
      <TextField
        label={t('meetings.searchLabel')}
        placeholder={t('meetings.searchPlaceholder')}
        value={query}
        onChangeText={setQuery}
        autoCapitalize="none"
        returnKeyType="search"
      />
      {meetings.status === 'loading' ? (
        <View style={{ gap: space[3] }}>
          <Skeleton height={112} />
          <Skeleton height={112} />
        </View>
      ) : (
        <View style={{ gap: space[3] }}>
          <Text variant="label" tone="muted" accessibilityLiveRegion="polite">
            {t('meetings.count', { count: visible.length })}
          </Text>
          {visible.length === 0 ? <Text tone="muted">{t('meetings.empty')}</Text> : null}
          {visible.map((meeting) => (
            <MeetingRow key={meeting.id} meeting={meeting} locale={locale} />
          ))}
        </View>
      )}
    </Screen>
  );
}
