'use client';

import { Microphone } from '@phosphor-icons/react';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { ErrorNotice, LinkButton, SectionHeader, Skeleton, Surface } from '@/components/ui/primitives';
import { formatDate } from '@/lib/i18n/format';
import { useChanges, useGraph, useMeetings } from '@/lib/queries';
import { usePrefs } from '@/providers/prefs';
import { useRouter } from 'next/navigation';
import styles from '../features.module.css';
import { MeetingRow, meetingHref } from '../meetings/MeetingRow';

type StatKey = 'meetings' | 'decisions' | 'links';

function Stat({ value, label }: { value: number; label: StatKey }) {
  const { t } = useTranslation();
  return (
    <div className={styles.stat}>
      <strong>{value}</strong>
      <span className={`${styles.muted} ${styles.small}`}>{t(`home.stats.${label}`, { count: value })}</span>
    </div>
  );
}

export function HomeScreen() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const router = useRouter();
  const meetings = useMeetings();
  const changes = useChanges();
  const graph = useGraph();

  const failed = [meetings, changes, graph].find((query) => query.isError);
  const decisions = meetings.data?.reduce((total, meeting) => total + meeting.decisionCount, 0) ?? 0;

  return (
    <>
      <PageHeader title={t('home.title')} />
      {failed ? (
        <ErrorNotice
          onRetry={() => {
            void failed.refetch();
          }}
        />
      ) : null}

      <Surface tone="accent" as="section" aria-labelledby="record-title">
        <h2 id="record-title">{t('home.record.title')}</h2>
        <p className={styles.muted}>{t('home.record.body')}</p>
        <div>
          <LinkButton href="/capture/" icon={Microphone}>
            {t('home.record.cta')}
          </LinkButton>
        </div>
      </Surface>

      {meetings.data && graph.data ? (
        <div className={styles.stats}>
          <Stat value={meetings.data.length} label="meetings" />
          <Stat value={decisions} label="decisions" />
          <Stat value={graph.data.edges.length} label="links" />
        </div>
      ) : (
        <Skeleton size="block" />
      )}

      {changes.isPending || (changes.data && changes.data.length > 0) ? (
        <section className={styles.section}>
          <SectionHeader title={t('home.changes.title')} />
          {changes.data ? (
            changes.data.map((change) => (
              <Link key={change.id} href={meetingHref(change.meetingId)} className={styles.changeLink}>
                <span>
                  {t('home.changes.replaces', { newLabel: change.newLabel, oldLabel: change.oldLabel })}
                </span>
                <span className="mono">{formatDate(change.date, locale, 'long')}</span>
              </Link>
            ))
          ) : (
            <Skeleton size="line" />
          )}
        </section>
      ) : null}

      <section className={styles.section}>
        <SectionHeader
          title={t('home.recent.title')}
          actionLabel={t('common.seeAll')}
          onAction={() => router.push('/meetings/')}
        />
        {meetings.data ? (
          <div className={styles.list}>
            {meetings.data.slice(0, 2).map((meeting) => (
              <MeetingRow key={meeting.id} meeting={meeting} />
            ))}
          </div>
        ) : (
          <Skeleton size="card" />
        )}
      </section>
    </>
  );
}
