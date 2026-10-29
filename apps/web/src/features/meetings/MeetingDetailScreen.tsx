'use client';

import { CheckCircle, Trash, TreeStructure } from '@phosphor-icons/react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import {
  Button,
  ErrorNotice,
  LinkButton,
  SectionHeader,
  Skeleton,
  Surface,
  Tag,
  TextField,
} from '@/components/ui/primitives';
import { SPEAKER_ERROR_KEYS, speakerLabel, validateSpeakerName } from '@/lib/domain/speakers';
import type { Meeting, Speaker } from '@/lib/domain/types';
import { formatClock, formatDate } from '@/lib/i18n/format';
import { useDeleteMeeting, useMeeting, useNameSpeaker } from '@/lib/queries';
import { usePrefs } from '@/providers/prefs';
import styles from '../features.module.css';

function SpeakerRow({
  meetingId,
  speaker,
  totalTalkMs,
}: {
  meetingId: string;
  speaker: Speaker;
  totalTalkMs: number;
}) {
  const { t } = useTranslation();
  const rename = useNameSpeaker(meetingId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(speaker.name ?? '');
  const normalized = draft.trim();
  const problem = validateSpeakerName(normalized);
  const unchanged = normalized === (speaker.name ?? '');
  const percent = totalTalkMs > 0 ? Math.round((speaker.talkMs / totalTalkMs) * 100) : 0;

  const save = (name: string | null) =>
    rename.mutate(
      { speakerId: speaker.id, name },
      {
        onSuccess: () => {
          setDraft(name ?? '');
          setEditing(false);
        },
      },
    );

  return (
    <Surface>
      <div className={styles.spread}>
        <h3>{speakerLabel([speaker], speaker.id, t)}</h3>
        <span className={styles.label}>{percent}%</span>
      </div>
      <meter
        className={styles.talk}
        min={0}
        max={100}
        value={percent}
        aria-label={t('meeting.talkShare', { percent })}
      />
      {editing ? (
        <div className={styles.stack}>
          <TextField
            label={t('meeting.speakerNameInput', { speaker: speaker.id })}
            value={draft}
            onChange={setDraft}
            maxLength={80}
            autoComplete="off"
            error={draft.length > 0 && problem ? t(SPEAKER_ERROR_KEYS[problem]) : undefined}
          />
          {rename.isError ? (
            <p className={`${styles.danger} ${styles.small}`} role="alert">
              {t('meeting.speakerSaveError')}
            </p>
          ) : null}
          <div className={styles.row}>
            <Button disabled={rename.isPending || !!problem || unchanged} onClick={() => save(normalized)}>
              {t('meeting.saveSpeakerName')}
            </Button>
            <Button
              variant="secondary"
              disabled={rename.isPending || speaker.name === null}
              onClick={() => save(null)}
            >
              {t('meeting.clearSpeakerName')}
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button
            variant="secondary"
            onClick={() => {
              setDraft(speaker.name ?? '');
              rename.reset();
              setEditing(true);
            }}
          >
            {speaker.name ? t('meeting.renameSpeaker') : t('meeting.nameSpeaker')}
          </Button>
        </div>
      )}
    </Surface>
  );
}

function DeleteMeeting({ meeting }: { meeting: Meeting }) {
  const { t } = useTranslation();
  const router = useRouter();
  const remove = useDeleteMeeting();
  const [open, setOpen] = useState(false);
  return (
    <section className={styles.section}>
      <SectionHeader title={t('meeting.delete.title')} />
      <div>
        <Button variant="secondary" icon={Trash} onClick={() => setOpen(true)}>
          {t('meeting.delete.button')}
        </Button>
      </div>
      <ConfirmDialog
        open={open}
        title={t('meeting.delete.confirmTitle')}
        confirmLabel={t('meeting.delete.confirm')}
        busy={remove.isPending}
        onCancel={() => setOpen(false)}
        onConfirm={() => remove.mutate(meeting.id, { onSuccess: () => router.replace('/meetings/') })}
      >
        <p>{t('meeting.delete.body')}</p>
        {remove.isError ? (
          <p className={styles.danger} role="alert">
            {t('meeting.delete.error')}
          </p>
        ) : null}
      </ConfirmDialog>
    </section>
  );
}

function Body({ meeting, highlight }: { meeting: Meeting; highlight?: string }) {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const totalTalkMs = meeting.speakers.reduce((total, speaker) => total + speaker.talkMs, 0);

  // Arriving from a cited answer: bring the cited sentence into view.
  useEffect(() => {
    if (highlight) document.getElementById(`segment-${highlight}`)?.scrollIntoView({ block: 'center' });
  }, [highlight, meeting.id]);

  return (
    <>
      <p className={`${styles.muted} ${styles.small}`}>
        {formatDate(meeting.startedAt, locale, 'long')},{' '}
        {t('meeting.duration', { count: meeting.durationMin })}
        {meeting.participants.length ? ` · ${meeting.participants.join(', ')}` : ''}
      </p>

      <div className={styles.split}>
        <div className={styles.stack}>
          <Surface as="section">
            <div className={styles.spread}>
              <h2>{t('meeting.summary')}</h2>
              <Tag label={t('common.aiDraft')} tone="accent" />
            </div>
            <p>{meeting.summary}</p>
          </Surface>

          <section className={styles.section}>
            <SectionHeader title={t('meeting.decisions')} />
            {meeting.decisions.map((decision) => (
              <div key={decision} className={styles.decision}>
                <CheckCircle size={22} weight="fill" aria-hidden />
                <span>{decision}</span>
              </div>
            ))}
          </section>

          <section className={styles.section}>
            <SectionHeader title={t('meeting.actions')} />
            {meeting.actions.map((action) => (
              <Surface key={action.id}>
                <h3>{action.text}</h3>
                {action.owner ? (
                  <p className={`${styles.muted} ${styles.small}`}>
                    {t('meeting.owner', { name: action.owner })}
                  </p>
                ) : null}
                {action.dueDate ? (
                  <p className={`${styles.muted} ${styles.small}`}>
                    {t('meeting.due', { date: formatDate(action.dueDate, locale, 'long') })}
                  </p>
                ) : null}
              </Surface>
            ))}
          </section>

          <section className={styles.section}>
            <SectionHeader title={t('meeting.speakers')} />
            {meeting.speakers.map((speaker) => (
              <SpeakerRow
                key={speaker.id}
                meetingId={meeting.id}
                speaker={speaker}
                totalTalkMs={totalTalkMs}
              />
            ))}
          </section>
        </div>

        <section className={styles.section}>
          <SectionHeader title={t('meeting.transcript')} />
          {meeting.segments.map((segment) => {
            const cited = segment.id === highlight;
            return (
              <article
                key={segment.id}
                id={`segment-${segment.id}`}
                className={`${styles.segment} ${cited ? styles.cited : ''}`}
              >
                <div className={styles.row}>
                  <span className={`mono ${styles.muted}`}>{formatClock(segment.startMs / 1000)}</span>
                  <span className={styles.label}>{speakerLabel(meeting.speakers, segment.speaker, t)}</span>
                  {cited ? <Tag label={t('meeting.citedSegment')} tone="accent" /> : null}
                </div>
                <p>{segment.text}</p>
              </article>
            );
          })}
        </section>
      </div>

      <div>
        <LinkButton href="/graph/" variant="secondary" icon={TreeStructure}>
          {t('meeting.openGraph')}
        </LinkButton>
      </div>
      <DeleteMeeting meeting={meeting} />
    </>
  );
}

export function MeetingDetailScreen() {
  const { t } = useTranslation();
  const params = useSearchParams();
  const id = params.get('id');
  const meeting = useMeeting(id);

  return (
    <>
      <PageHeader title={meeting.data?.title ?? ''} backHref="/meetings/" />
      {meeting.isPending && id ? <Skeleton size="tall" /> : null}
      {meeting.isError ? (
        <ErrorNotice
          onRetry={() => {
            void meeting.refetch();
          }}
        />
      ) : null}
      {(!id || (meeting.isSuccess && !meeting.data)) && (
        <p className={styles.muted}>{t('meeting.notFound')}</p>
      )}
      {meeting.data ? (
        <Body key={meeting.data.id} meeting={meeting.data} highlight={params.get('seg') ?? undefined} />
      ) : null}
    </>
  );
}
