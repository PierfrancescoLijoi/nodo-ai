import { useRouter } from 'expo-router';
import { Microphone } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { useAsync } from '@/data/RepositoryProvider';
import { formatDate } from '@/i18n/format';
import { usePrefs } from '@/prefs/PrefsProvider';
import { space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { MeetingRow } from '@/ui/MeetingRow';
import { Screen } from '@/ui/Screen';
import { SectionHeader } from '@/ui/SectionHeader';
import { SettingsButton } from '@/ui/SettingsButton';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

type StatKey = 'meetings' | 'decisions' | 'links';

function Stat({ value, labelKey }: { value: number; labelKey: StatKey }) {
  const { t } = useTranslation();
  return (
    <View style={{ flex: 1, gap: space[1] }}>
      <Text variant="title">{value}</Text>
      <Text variant="small" tone="muted">
        {t(`home.stats.${labelKey}`, { count: value })}
      </Text>
    </View>
  );
}

export default function HomeScreen() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const router = useRouter();
  const meetings = useAsync((r) => r.listMeetings());
  const changes = useAsync((r) => r.listChanges());
  const graph = useAsync((r) => r.getGraph());

  const decisionCount = meetings.status === 'ready' ? meetings.data.reduce((n, m) => n + m.decisions.length, 0) : 0;

  return (
    <Screen wordmark title={t('home.title')} right={<SettingsButton />}>
      <Surface tone="accent">
        <Text variant="title">{t('home.record.title')}</Text>
        <Text tone="muted">{t('home.record.body')}</Text>
        <Button label={t('home.record.cta')} icon={Microphone} onPress={() => router.push('/capture')} />
      </Surface>

      {meetings.status === 'ready' && graph.status === 'ready' ? (
        <View style={{ flexDirection: 'row', gap: space[4] }}>
          <Stat value={meetings.data.length} labelKey="meetings" />
          <Stat value={decisionCount} labelKey="decisions" />
          <Stat value={graph.data.edges.length} labelKey="links" />
        </View>
      ) : (
        <Skeleton height={52} />
      )}

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('home.changes.title')} />
        {changes.status === 'ready' ? (
          changes.data.map((change) => (
            <Pressable
              key={change.id}
              accessibilityRole="button"
              onPress={() => router.push({ pathname: '/meeting/[id]', params: { id: change.meetingId } })}
              style={{ gap: space[1], paddingVertical: space[2] }}
            >
              <Text>{change.text}</Text>
              <Text variant="mono" tone="muted">
                {formatDate(change.date, locale, 'long')}
              </Text>
            </Pressable>
          ))
        ) : (
          <Skeleton height={44} />
        )}
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader
          title={t('home.recent.title')}
          actionLabel={t('common.seeAll')}
          onAction={() => router.push('/meetings')}
        />
        {meetings.status === 'ready' ? (
          meetings.data.slice(0, 2).map((meeting) => <MeetingRow key={meeting.id} meeting={meeting} locale={locale} />)
        ) : (
          <Skeleton height={120} />
        )}
      </View>
    </Screen>
  );
}
