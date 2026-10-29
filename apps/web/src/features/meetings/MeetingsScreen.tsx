'use client';

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { ErrorNotice, Skeleton, TextField } from '@/components/ui/primitives';
import { foldText } from '@/lib/i18n/format';
import { useMeetings } from '@/lib/queries';
import { usePrefs } from '@/providers/prefs';
import styles from '../features.module.css';
import { MeetingRow } from './MeetingRow';

export function MeetingsScreen() {
  const { t } = useTranslation();
  const { locale } = usePrefs();
  const meetings = useMeetings();
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    const needle = foldText(query.trim(), locale);
    return (meetings.data ?? []).filter((meeting) =>
      foldText(
        [meeting.title, meeting.tags.join(' '), meeting.participants.join(' ')].join(' '),
        locale,
      ).includes(needle),
    );
  }, [meetings.data, query, locale]);

  return (
    <>
      <PageHeader title={t('meetings.title')} />
      <TextField
        label={t('meetings.searchLabel')}
        placeholder={t('meetings.searchPlaceholder')}
        value={query}
        onChange={setQuery}
        type="search"
        autoComplete="off"
      />
      {meetings.isError ? (
        <ErrorNotice
          onRetry={() => {
            void meetings.refetch();
          }}
        />
      ) : null}
      {meetings.isPending ? (
        <div className={styles.list}>
          <Skeleton size="card" />
          <Skeleton size="card" />
        </div>
      ) : null}
      {meetings.data ? (
        <div className={styles.section}>
          <p className={styles.label} role="status">
            {t('meetings.count', { count: visible.length })}
          </p>
          {visible.length === 0 ? <p className={styles.muted}>{t('meetings.empty')}</p> : null}
          <div className={styles.list}>
            {visible.map((meeting) => (
              <MeetingRow key={meeting.id} meeting={meeting} />
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}
