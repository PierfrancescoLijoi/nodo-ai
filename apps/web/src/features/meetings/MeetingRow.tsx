'use client';

import Link from 'next/link';
import { useTranslation } from 'react-i18next';

import { Tag } from '@/components/ui/primitives';
import type { MeetingSummary } from '@/lib/domain/types';
import { formatDate } from '@/lib/i18n/format';
import { usePrefs } from '@/providers/prefs';
import styles from '../features.module.css';

export function meetingHref(id: string, segmentId?: string): string {
  const query = new URLSearchParams({ id });
  if (segmentId) query.set('seg', segmentId);
  return `/meeting/?${query.toString()}`;
}

export function MeetingRow({ meeting }: { meeting: MeetingSummary }) {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  return (
    <Link href={meetingHref(meeting.id)} className={styles.meetingRow}>
      <div className={styles.spread}>
        <h3>{meeting.title}</h3>
        {meeting.state === 'ready' ? null : (
          <Tag label={t(`meeting.state.${meeting.state}`)} tone="warning" />
        )}
      </div>
      <span className={`${styles.muted} ${styles.small}`}>
        {formatDate(meeting.startedAt, locale, 'long')},{' '}
        {t('meeting.duration', { count: meeting.durationMin })}
      </span>
      {meeting.summary ? <p className={styles.muted}>{meeting.summary}</p> : null}
      <div className={styles.row}>
        {meeting.tags.slice(0, 4).map((tag) => (
          <Tag key={tag} label={tag} />
        ))}
      </div>
    </Link>
  );
}
