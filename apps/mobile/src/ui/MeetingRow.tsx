import { useRouter } from 'expo-router';
import { CaretRight } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import type { MeetingSummary } from '@/data/types';
import type { Locale } from '@/i18n';
import { formatDate } from '@/i18n/format';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space } from '@/theme/tokens';

import { Tag } from './Tag';
import { Text } from './Text';

export function MeetingRow({ meeting, locale }: { meeting: MeetingSummary; locale: Locale }) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const router = useRouter();
  const when = `${formatDate(meeting.startedAt, locale, 'long')}, ${t('meeting.duration', { count: meeting.durationMin })}`;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[meeting.title, when, meeting.participants.join(', '), meeting.tags.join(', ')].join('. ')}
      onPress={() => router.push({ pathname: '/meeting/[id]', params: { id: meeting.id } })}
      style={({ pressed }) => ({
        flexDirection: 'row',
        alignItems: 'center',
        gap: space[3],
        padding: space[4],
        borderRadius: radius.card,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: pressed ? colors.surfaceRaised : colors.surface,
      })}
    >
      <View style={{ flex: 1, gap: space[2] }}>
        <Text variant="heading">{meeting.title}</Text>
        {meeting.state !== 'ready' ? <Tag label={t(`meeting.state.${meeting.state}`)} tone="accent" /> : null}
        <Text variant="small" tone="muted">
          {when}
        </Text>
        <Text variant="small" tone="muted">
          {meeting.participants.join(', ')}
        </Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[2] }}>
          {meeting.tags.map((tag) => (
            <Tag key={tag} label={tag} />
          ))}
        </View>
      </View>
      <CaretRight size={20} color={colors.textMuted} weight="bold" />
    </Pressable>
  );
}
