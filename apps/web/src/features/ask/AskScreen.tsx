'use client';

import { ArrowUpRight, PaperPlaneTilt, Warning } from '@phosphor-icons/react';
import Link from 'next/link';
import { useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag, TextField } from '@/components/ui/primitives';
import { speakerLabel } from '@/lib/domain/speakers';
import type { Answer, Citation } from '@/lib/domain/types';
import { formatClock } from '@/lib/i18n/format';
import { useMeeting, useMeetings } from '@/lib/queries';
import { usePrefs } from '@/providers/prefs';
import { useRepository } from '@/providers/data';
import styles from '../features.module.css';
import { meetingHref } from '../meetings/MeetingRow';

interface Turn {
  id: number;
  question: string;
  state: 'pending' | 'done' | 'failed';
  answer?: Answer;
}

function Source({ citation, title }: { citation: Citation; title?: string }) {
  const { t } = useTranslation();
  const meeting = useMeeting(citation.meetingId);
  const speakers = meeting.data?.speakers ?? [];
  return (
    <Link href={meetingHref(citation.meetingId, citation.segmentId)} className={styles.source}>
      <span className={styles.spread}>
        <span className={styles.label}>
          {[title, formatClock(citation.startMs / 1000)].filter(Boolean).join(', ')}
        </span>
        <ArrowUpRight size={16} weight="bold" aria-hidden />
      </span>
      <span className={`${styles.muted} ${styles.small}`}>
        {speakerLabel(speakers, citation.speaker, t)}: {citation.quote}
      </span>
    </Link>
  );
}

function AnswerCard({
  turn,
  titles,
  onRetry,
}: {
  turn: Turn;
  titles: Map<string, string>;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { answer } = turn;
  const unanswerable = answer?.answerability === 'unanswerable';
  return (
    <Surface tone={unanswerable || answer?.degraded ? 'warning' : 'default'} as="article">
      <p className={styles.label}>{turn.question}</p>
      {turn.state === 'failed' ? <ErrorNotice onRetry={onRetry} /> : null}
      {turn.state === 'pending' ? (
        <div className={styles.stack} role="status">
          <span className={`${styles.muted} ${styles.small}`}>{t('ask.thinking')}</span>
          <Skeleton size="line" />
        </div>
      ) : null}
      {answer ? (
        <>
          {unanswerable ? (
            <div className={styles.row}>
              <Warning size={20} weight="fill" className={styles.warningText} aria-hidden />
              <h3 className={styles.warningText}>{t('ask.unanswerableTitle')}</h3>
            </div>
          ) : null}
          {answer.degraded ? <Tag label={t('web.degraded')} tone="warning" /> : null}
          <p>{unanswerable ? t('ask.unanswerableBody') : answer.text}</p>
          {answer.citations.length > 0 ? (
            <div className={styles.stack}>
              <p className={styles.label}>{t('ask.sources')}</p>
              {answer.citations.map((citation) => (
                <Source key={citation.segmentId} citation={citation} title={titles.get(citation.meetingId)} />
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </Surface>
  );
}

// Keyed by locale in AskScreen: a language switch starts a fresh conversation instead of mixing languages.
function Conversation() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const repository = useRepository();
  const meetings = useMeetings();
  const [text, setText] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextId = useRef(0);

  const titles = new Map((meetings.data ?? []).map((meeting) => [meeting.id, meeting.title]));

  const update = (id: number, patch: Partial<Turn>) =>
    setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));

  const run = (id: number, question: string) => {
    update(id, { state: 'pending', answer: undefined });
    repository.ask(question, locale).then(
      (answer) => update(id, { state: 'done', answer }),
      () => update(id, { state: 'failed' }),
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = text.trim();
    if (!question) return;
    const id = nextId.current++;
    setTurns((current) => [{ id, question, state: 'pending' }, ...current]);
    setText('');
    run(id, question);
  };

  return (
    <>
      <form className={styles.askForm} onSubmit={submit}>
        <TextField
          label={t('ask.inputLabel')}
          value={text}
          placeholder={t('ask.inputPlaceholder')}
          onChange={setText}
          autoComplete="off"
        />
        <Button type="submit" icon={PaperPlaneTilt} disabled={!text.trim()} aria-label={t('ask.send')} />
      </form>
      <div className={styles.stack} aria-live="polite">
        {turns.map((turn) => (
          <AnswerCard key={turn.id} turn={turn} titles={titles} onRetry={() => run(turn.id, turn.question)} />
        ))}
      </div>
    </>
  );
}

export function AskScreen() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  return (
    <>
      <PageHeader title={t('ask.title')} intro={t('ask.intro')} />
      <Conversation key={locale} />
    </>
  );
}
