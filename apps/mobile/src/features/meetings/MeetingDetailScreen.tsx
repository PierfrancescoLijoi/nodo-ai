import { useLocalSearchParams, useRouter } from 'expo-router';
import { CheckCircle, TreeStructure } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useAsync } from '@/data/RepositoryProvider';
import type { Meeting } from '@/data/types';
import { formatClock, formatDate } from '@/i18n/format';
import { usePrefs } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { SectionHeader } from '@/ui/SectionHeader';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Tag } from '@/ui/Tag';
import { Text } from '@/ui/Text';

function Body({ meeting, highlight }: { meeting: Meeting; highlight?: string }) {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const { colors } = useTheme();
  const router = useRouter();

  return (
    <>
      <View style={{ gap: space[2] }}>
        <Text variant="small" tone="muted">
          {formatDate(meeting.startedAt, locale, 'long')}, {t('meeting.duration', { count: meeting.durationMin })}
        </Text>
        <Text variant="small" tone="muted">
          {meeting.participants.join(', ')}
        </Text>
      </View>

      <Surface>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text variant="heading">{t('meeting.summary')}</Text>
          <Tag label={t('common.aiDraft')} tone="accent" />
        </View>
        <Text>{meeting.summary}</Text>
      </Surface>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('meeting.decisions')} />
        {meeting.decisions.map((decision) => (
          <View key={decision} style={{ flexDirection: 'row', gap: space[3], alignItems: 'flex-start' }}>
            <CheckCircle size={22} color={colors.accent} weight="fill" style={{ marginTop: 1 }} />
            <Text style={{ flex: 1 }}>{decision}</Text>
          </View>
        ))}
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('meeting.actions')} />
        {meeting.actions.map((action) => (
          <Surface key={action.id}>
            <Text variant="heading">{action.text}</Text>
            <Text variant="small" tone="muted">
              {t('meeting.owner', { name: action.owner })}
            </Text>
            <Text variant="small" tone="muted">
              {t('meeting.due', { date: formatDate(action.dueDate, locale, 'long') })}
            </Text>
          </Surface>
        ))}
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('meeting.transcript')} />
        {meeting.segments.map((segment) => {
          const cited = segment.id === highlight;
          return (
            <View
              key={segment.id}
              style={{
                gap: space[2],
                padding: space[4],
                borderRadius: radius.card,
                backgroundColor: cited ? colors.accentSoft : colors.surface,
                borderWidth: 1,
                borderColor: cited ? colors.accent : colors.border,
              }}
            >
              <View style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
                <Text variant="mono" tone="muted">
                  {formatClock(segment.startMs / 1000)}
                </Text>
                <Text variant="label">{segment.speaker}</Text>
                {cited ? <Tag label={t('meeting.citedSegment')} tone="accent" /> : null}
              </View>
              <Text>{segment.text}</Text>
            </View>
          );
        })}
      </View>

      <Button
        variant="secondary"
        icon={TreeStructure}
        label={t('meeting.openGraph')}
        onPress={() => router.push('/graph')}
      />
    </>
  );
}

export default function MeetingDetailScreen() {
  const { id, seg } = useLocalSearchParams<{ id: string; seg?: string }>();
  const { t } = useTranslation();
  const meeting = useAsync((r) => r.getMeeting(String(id)), [id]);

  return (
    <Screen back title={meeting.status === 'ready' ? meeting.data?.title : undefined} bottomInset>
      {meeting.status === 'loading' ? <Skeleton height={160} /> : null}
      {meeting.status === 'error' ? <ErrorNotice onRetry={meeting.retry} /> : null}
      {meeting.status === 'ready' && !meeting.data ? <Text tone="muted">{t('meeting.notFound')}</Text> : null}
      {meeting.status === 'ready' && meeting.data ? <Body meeting={meeting.data} highlight={seg} /> : null}
    </Screen>
  );
}
