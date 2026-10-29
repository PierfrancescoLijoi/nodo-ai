import { useLocalSearchParams, useRouter } from 'expo-router';
import { CheckCircle, Trash, TreeStructure } from '@/ui/icons';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useAsync, useRepository } from '@/data/RepositoryProvider';
import { speakerLabel, validateSpeakerName, type SpeakerNameValidationError } from '@/data/speakerName';
import type { Meeting, Speaker } from '@/data/types';
import { formatClock, formatDate } from '@/i18n/format';
import { usePrefs } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { SectionHeader } from '@/ui/SectionHeader';
import { useLayout } from '@/ui/useLayout';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Tag } from '@/ui/Tag';
import { Text } from '@/ui/Text';
import { TextField } from '@/ui/TextField';

const VALIDATION_KEYS: Record<
  SpeakerNameValidationError,
  | 'meeting.speakerNameRequired'
  | 'meeting.speakerNameTooLong'
  | 'meeting.speakerNameTooManyWords'
  | 'meeting.speakerNameInvalidCharacters'
> = {
  required: 'meeting.speakerNameRequired',
  tooLong: 'meeting.speakerNameTooLong',
  tooManyWords: 'meeting.speakerNameTooManyWords',
  invalidCharacters: 'meeting.speakerNameInvalidCharacters',
};

function SpeakerRow({
  meetingId,
  speaker,
  totalTalkMs,
  onUpdated,
}: {
  meetingId: string;
  speaker: Speaker;
  totalTalkMs: number;
  onUpdated: (speakers: Speaker[]) => void;
}) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const repository = useRepository();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(speaker.name ?? '');
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const normalized = draft.trim();
  const validation = validateSpeakerName(normalized);
  const unchanged = normalized === (speaker.name ?? '');
  const percent = totalTalkMs > 0 ? Math.round((speaker.talkMs / totalTalkMs) * 100) : 0;

  const save = async (name: string | null) => {
    setSaving(true);
    setSaveFailed(false);
    try {
      const updated = await repository.nameSpeaker(meetingId, speaker.id, name);
      onUpdated(updated.speakers);
      setDraft(name ?? '');
      setEditing(false);
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Surface>
      <View style={{ gap: space[3] }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space[3] }}>
          <Text variant="heading" style={{ flex: 1 }}>
            {speakerLabel([speaker], speaker.id, t)}
          </Text>
          <Text variant="label" tone="muted">
            {percent}%
          </Text>
        </View>
        <View
          accessibilityLabel={t('meeting.talkShare', { percent })}
          style={{ height: 4, borderRadius: radius.pill, backgroundColor: colors.border, overflow: 'hidden' }}
        >
          <View style={{ width: `${percent}%`, height: '100%', backgroundColor: colors.accent }} />
        </View>
        {editing ? (
          <View style={{ gap: space[2] }}>
            <TextField
              label={t('meeting.speakerNameInput', { speaker: speaker.id })}
              value={draft}
              onChangeText={setDraft}
              maxLength={80}
              autoCapitalize="words"
              autoCorrect={false}
            />
            {validation ? (
              <Text variant="small" tone="danger">
                {t(VALIDATION_KEYS[validation])}
              </Text>
            ) : null}
            {saveFailed ? (
              <Text variant="small" tone="danger">
                {t('meeting.speakerSaveError')}
              </Text>
            ) : null}
            <View style={{ flexDirection: 'row', gap: space[2], flexWrap: 'wrap' }}>
              <Button
                label={t('meeting.saveSpeakerName')}
                onPress={() => void save(normalized)}
                disabled={saving || !!validation || unchanged}
              />
              <Button
                label={t('meeting.clearSpeakerName')}
                variant="secondary"
                onPress={() => void save(null)}
                disabled={saving || speaker.name === null}
              />
            </View>
          </View>
        ) : (
          <Button
            label={speaker.name ? t('meeting.renameSpeaker') : t('meeting.nameSpeaker')}
            variant="secondary"
            onPress={() => {
              setDraft(speaker.name ?? '');
              setSaveFailed(false);
              setEditing(true);
            }}
          />
        )}
      </View>
    </Surface>
  );
}

function Body({ meeting, highlight }: { meeting: Meeting; highlight?: string }) {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const { colors } = useTheme();
  const router = useRouter();
  const repository = useRepository();
  const [speakers, setSpeakers] = useState(meeting.speakers);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteFailed, setDeleteFailed] = useState(false);
  const { isDesktop } = useLayout();
  const totalTalkMs = speakers.reduce((total, speaker) => total + speaker.talkMs, 0);

  const overview = (
    <>
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
            {action.owner ? (
              <Text variant="small" tone="muted">
                {t('meeting.owner', { name: action.owner })}
              </Text>
            ) : null}
            {action.dueDate ? (
              <Text variant="small" tone="muted">
                {t('meeting.due', { date: formatDate(action.dueDate, locale, 'long') })}
              </Text>
            ) : null}
          </Surface>
        ))}
      </View>

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('meeting.speakers')} />
        {speakers.map((speaker) => (
          <SpeakerRow
            key={speaker.id}
            meetingId={meeting.id}
            speaker={speaker}
            totalTalkMs={totalTalkMs}
            onUpdated={setSpeakers}
          />
        ))}
      </View>
    </>
  );
  const transcript = (
    <>
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
                <Text variant="label">{speakerLabel(speakers, segment.speaker, t)}</Text>
                {cited ? <Tag label={t('meeting.citedSegment')} tone="accent" /> : null}
              </View>
              <Text>{segment.text}</Text>
            </View>
          );
        })}
      </View>
    </>
  );

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

      {isDesktop ? (
        <View style={{ flexDirection: 'row', gap: space[8], alignItems: 'flex-start' }}>
          <View style={{ flex: 2, gap: space[6] }}>{overview}</View>
          <View style={{ flex: 3, gap: space[3] }}>{transcript}</View>
        </View>
      ) : (
        <>
          {overview}
          {transcript}
        </>
      )}

      <Button
        variant="secondary"
        icon={TreeStructure}
        label={t('meeting.openGraph')}
        onPress={() => router.push('/graph')}
      />

      <View style={{ gap: space[3] }}>
        <SectionHeader title={t('meeting.delete.title')} />
        {!confirmDelete ? (
          <Button
            variant="secondary"
            icon={Trash}
            label={t('meeting.delete.button')}
            onPress={() => setConfirmDelete(true)}
          />
        ) : (
          <Surface tone="warning">
            <Text variant="heading" tone="warning">
              {t('meeting.delete.confirmTitle')}
            </Text>
            <Text>{t('meeting.delete.body')}</Text>
            {deleteFailed ? <Text tone="danger">{t('meeting.delete.error')}</Text> : null}
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[2] }}>
              <Button
                label={t('meeting.delete.confirm')}
                disabled={deleting}
                onPress={() => {
                  setDeleting(true);
                  setDeleteFailed(false);
                  void repository.deleteMeeting(meeting.id).then(
                    () => router.replace('/meetings'),
                    () => {
                      setDeleting(false);
                      setDeleteFailed(true);
                    },
                  );
                }}
              />
              <Button
                variant="secondary"
                label={t('common.cancel')}
                disabled={deleting}
                onPress={() => setConfirmDelete(false)}
              />
            </View>
          </Surface>
        )}
      </View>
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
      {meeting.status === 'ready' && meeting.data ? (
        <Body key={meeting.data.id} meeting={meeting.data} highlight={seg} />
      ) : null}
    </Screen>
  );
}
