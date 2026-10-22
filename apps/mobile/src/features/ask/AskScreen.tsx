import { useRouter } from 'expo-router';
import { ArrowUpRight, PaperPlaneTilt, Warning } from 'phosphor-react-native';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, Pressable, View } from 'react-native';

import { useAsync, useRepository } from '@/data/RepositoryProvider';
import type { Answer, QuestionId } from '@/data/types';
import { formatClock } from '@/i18n/format';
import { usePrefs } from '@/prefs/PrefsProvider';
import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { Screen } from '@/ui/Screen';
import { SettingsButton } from '@/ui/SettingsButton';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';
import { TextField } from '@/ui/TextField';

const SUGGESTIONS: QuestionId[] = ['ingestionChange', 'saraTasks', 'ragChoice'];
let nextTurnId = 0;

interface Turn {
  id: number;
  question: string;
  questionId?: QuestionId;
  answer?: Answer;
  failed?: boolean;
}

function AnswerCard({ turn, titles, onRetry }: { turn: Turn; titles: Record<string, string>; onRetry: () => void }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const router = useRouter();
  const { answer } = turn;

  return (
    <Surface tone={answer?.answerability === 'unanswerable' ? 'warning' : 'default'}>
      <Text variant="label" tone="muted">
        {turn.question}
      </Text>
      {turn.failed ? (
        <ErrorNotice onRetry={onRetry} />
      ) : !answer ? (
        <View style={{ gap: space[2] }}>
          <Text variant="small" tone="muted">
            {t('ask.thinking')}
          </Text>
          <Skeleton height={14} />
          <Skeleton height={14} width="70%" />
        </View>
      ) : (
        <>
          {answer.answerability === 'unanswerable' ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[2] }}>
              <Warning size={20} color={colors.warning} weight="fill" />
              <Text variant="heading" tone="warning">
                {t('ask.unanswerableTitle')}
              </Text>
            </View>
          ) : null}
          <Text>{answer.text}</Text>
          {answer.citations.length > 0 ? (
            <View style={{ gap: space[2] }}>
              <Text variant="label" tone="muted">
                {t('ask.sources')}
              </Text>
              {answer.citations.map((citation) => (
                <Pressable
                  key={citation.segmentId}
                  accessibilityRole="link"
                  onPress={() =>
                    router.push({
                      pathname: '/meeting/[id]',
                      params: { id: citation.meetingId, seg: citation.segmentId },
                    })
                  }
                  style={({ pressed }) => ({
                    gap: space[1],
                    padding: space[3],
                    borderRadius: radius.control,
                    backgroundColor: pressed ? colors.accentSoft : colors.surfaceRaised,
                  })}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text variant="label">
                      {[titles[citation.meetingId], formatClock(citation.startMs / 1000)].filter(Boolean).join(', ')}
                    </Text>
                    <ArrowUpRight size={16} color={colors.accent} weight="bold" />
                  </View>
                  <Text variant="small" tone="muted">
                    {citation.speaker}: {citation.quote}
                  </Text>
                </Pressable>
              ))}
            </View>
          ) : null}
        </>
      )}
    </Surface>
  );
}

// Keyed by locale: a language switch starts a fresh conversation instead of mixing languages.
export default function AskScreen() {
  const { locale } = usePrefs();
  return <AskConversation key={locale} />;
}

function AskConversation() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const repository = useRepository();
  const meetings = useAsync((r) => r.listMeetings());
  const [text, setText] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);

  const titles = meetings.status === 'ready' ? Object.fromEntries(meetings.data.map((m) => [m.id, m.title])) : {};

  const update = (id: number, patch: Partial<Turn>) =>
    setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));

  const run = (id: number, question: string, questionId?: QuestionId) => {
    update(id, { failed: false, answer: undefined });
    repository.ask({ questionId, text: question }).then(
      (answer) => {
        update(id, { answer });
        AccessibilityInfo.announceForAccessibility(answer.text);
      },
      () => update(id, { failed: true }),
    );
  };

  const submit = (question: string, questionId?: QuestionId) => {
    const trimmed = question.trim();
    if (!trimmed) return;
    const id = ++nextTurnId;
    setTurns((current) => [{ id, question: trimmed, questionId }, ...current]);
    setText('');
    run(id, trimmed, questionId);
  };

  return (
    <Screen title={t('ask.title')} intro={t('ask.intro')} right={<SettingsButton />}>
      <View style={{ gap: space[2] }}>
        <Text variant="label" tone="muted">
          {t('ask.suggestionsLabel')}
        </Text>
        {SUGGESTIONS.map((id) => (
          <Pressable
            key={id}
            accessibilityRole="button"
            onPress={() => submit(t(`ask.suggestions.${id}`), id)}
            style={({ pressed }) => ({
              minHeight: touch,
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: space[3],
              paddingHorizontal: space[4],
              paddingVertical: space[3],
              borderRadius: radius.control,
              borderWidth: 1,
              borderColor: colors.border,
              backgroundColor: pressed ? colors.surfaceRaised : colors.surface,
            })}
          >
            <Text style={{ flex: 1 }}>{t(`ask.suggestions.${id}`)}</Text>
            <ArrowUpRight size={18} color={colors.accent} weight="bold" />
          </Pressable>
        ))}
      </View>

      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: space[2] }}>
        <View style={{ flex: 1 }}>
          <TextField
            label={t('ask.inputLabel')}
            placeholder={t('ask.inputPlaceholder')}
            value={text}
            onChangeText={setText}
            onSubmitEditing={() => submit(text)}
            returnKeyType="send"
          />
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t('ask.send')}
          disabled={!text.trim()}
          onPress={() => submit(text)}
          style={{
            width: touch,
            height: touch,
            borderRadius: radius.control,
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: colors.accent,
            opacity: text.trim() ? 1 : 0.4,
          }}
        >
          <PaperPlaneTilt size={22} color={colors.accentText} weight="fill" />
        </Pressable>
      </View>

      <View style={{ gap: space[3] }}>
        {turns.map((turn) => (
          <AnswerCard
            key={turn.id}
            turn={turn}
            titles={titles}
            onRetry={() => run(turn.id, turn.question, turn.questionId)}
          />
        ))}
      </View>
      <Text variant="small" tone="muted">
        {t('ask.footnote')}
      </Text>
    </Screen>
  );
}
