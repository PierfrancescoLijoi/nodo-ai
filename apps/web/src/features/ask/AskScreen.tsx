'use client';

import { ArrowUpRight, Microphone, PaperPlaneTilt, Warning } from '@phosphor-icons/react';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag, TextField } from '@/components/ui/primitives';
import { speakerLabel } from '@/lib/domain/speakers';
import type { Answer, Citation } from '@/lib/domain/types';
import { formatClock } from '@/lib/i18n/format';
import { useMeeting, useMeetings } from '@/lib/queries';
import { usePrefs } from '@/providers/prefs';
import { useRepository, useVoiceApi } from '@/providers/data';
import styles from '../features.module.css';
import { BrowserPcmCapture } from '../capture/pcm';
import { VoiceSessionClient, type VoiceEvent } from '../capture/voiceClient';
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
  const voice = useVoiceApi();
  const searchParams = useSearchParams();
  const meetings = useMeetings();
  const [text, setText] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [dictating, setDictating] = useState(false);
  const [dictationError, setDictationError] = useState(false);
  const nextId = useRef(0);
  const dictationHeld = useRef(false);
  const dictationClient = useRef<VoiceSessionClient | undefined>(undefined);
  const dictationCapture = useRef<BrowserPcmCapture | undefined>(undefined);
  const nodeLabel = searchParams.get('node');
  const meetingIds = (searchParams.get('meetings') ?? '').split(',').filter(Boolean);

  const titles = new Map((meetings.data ?? []).map((meeting) => [meeting.id, meeting.title]));

  const update = (id: number, patch: Partial<Turn>) =>
    setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));

  const run = (id: number, question: string) => {
    update(id, { state: 'pending', answer: undefined });
    repository.ask(question, locale, meetingIds).then(
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

  const finishDictation = useCallback(() => {
    dictationHeld.current = false;
    if (!dictationClient.current && !dictationCapture.current) return;
    void dictationCapture.current?.stop();
    dictationClient.current?.end();
    dictationClient.current = undefined;
    dictationCapture.current = undefined;
    setDictating(false);
  }, []);

  const startDictation = async () => {
    if (dictating) return;
    setDictationError(false);
    try {
      const session = await voice.openDictation(locale);
      const capture = new BrowserPcmCapture();
      const client = new VoiceSessionClient(
        session,
        (event: VoiceEvent) => {
          if (event.type === 'transcript.final' && typeof event.text === 'string') {
            setText((current) => [current, event.text].filter(Boolean).join(' ').trim());
            finishDictation();
          }
        },
        {
          refreshTicket: voice.refreshTicket,
          onError: () => {
            setDictationError(true);
            finishDictation();
          },
        },
      );
      dictationClient.current = client;
      dictationCapture.current = capture;
      await capture.start(({ offsetMs, pcm }) => client.sendPcm(offsetMs, pcm), () => undefined);
      if (!dictationHeld.current) {
        await capture.stop();
        dictationClient.current = undefined;
        dictationCapture.current = undefined;
        return;
      }
      client.startPushToTalk();
      client.connect();
      setDictating(true);
    } catch {
      finishDictation();
      setDictationError(true);
    }
  };

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === 'Escape') finishDictation();
    };
    window.addEventListener('keydown', cancel);
    return () => {
      window.removeEventListener('keydown', cancel);
      finishDictation();
    };
  }, [finishDictation]);

  return (
    <>
      {nodeLabel && meetingIds.length ? (
        <Surface tone="accent" as="section">
          <p className={styles.label}>{t('ask.nodeContext')}</p>
          <p>{t('ask.nodeContextBody', { node: nodeLabel })}</p>
        </Surface>
      ) : null}
      <form className={styles.askForm} onSubmit={submit}>
        <TextField
          label={t('ask.inputLabel')}
          value={text}
          placeholder={t('ask.inputPlaceholder')}
          onChange={setText}
          autoComplete="off"
        />
        <div className={styles.row}>
          <Button
            variant={dictating ? 'danger' : 'secondary'}
            icon={Microphone}
            aria-label={t('ask.dictate')}
            aria-pressed={dictating}
            onPointerDown={(event) => {
              event.preventDefault();
              dictationHeld.current = true;
              void startDictation();
            }}
            onPointerUp={finishDictation}
            onPointerCancel={finishDictation}
            onKeyDown={(event) => {
              if (!event.repeat && (event.key === ' ' || event.key === 'Enter')) {
                dictationHeld.current = true;
                void startDictation();
              }
            }}
            onKeyUp={(event) => {
              if (event.key === ' ' || event.key === 'Enter') finishDictation();
            }}
          >
            {dictating ? t('ask.dictating') : t('ask.dictate')}
          </Button>
          <Button type="submit" icon={PaperPlaneTilt} disabled={!text.trim()} aria-label={t('ask.send')} />
        </div>
      </form>
      {dictationError ? (
        <Surface tone="warning">
          <p role="alert">{t('ask.dictationUnavailable')}</p>
        </Surface>
      ) : null}
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
