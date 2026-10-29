'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import type { ReviewDecision, ReviewItem } from '@/lib/domain/types';
import { useReviewDecision, useReviews } from '@/lib/queries';
import styles from '../features.module.css';
import { meetingHref } from '../meetings/MeetingRow';

/** One proposal in plain words: what it connects, how sure the system was, and the two ways to answer. */
export function ReviewCard({
  item,
  busy,
  onDecide,
}: {
  item: ReviewItem;
  busy: boolean;
  onDecide(decision: ReviewDecision): void;
}) {
  const { t } = useTranslation();
  const sentence = t(`graph.relation.${item.kind}`, { from: item.from.label, to: item.to.label });
  return (
    <Surface as="li">
      <h3>{sentence}</h3>
      <div className={styles.row}>
        <Tag label={t(`graph.types.${item.from.type}`)} />
        <Tag label={t(`graph.types.${item.to.type}`)} />
        <Tag label={t('review.confidence', { percent: Math.round(item.confidence * 100) })} tone="accent" />
      </div>
      <div className={styles.row}>
        <Button
          disabled={busy}
          aria-label={`${t('review.approve')}: ${sentence}`}
          onClick={() => onDecide('approved')}
        >
          {t('review.approve')}
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          aria-label={`${t('review.reject')}: ${sentence}`}
          onClick={() => onDecide('rejected')}
        >
          {t('review.reject')}
        </Button>
        {item.meetingId ? <Link href={meetingHref(item.meetingId)}>{t('review.openSource')}</Link> : null}
      </div>
    </Surface>
  );
}

const INBOX_LIMIT = 100;

/** The inbox: what the system proposed and a person has not yet decided (ADR-004, ADR-025). */
export function ReviewScreen() {
  const { t } = useTranslation();
  const reviews = useReviews();
  const decide = useReviewDecision();
  const [busyId, setBusyId] = useState<string>();

  const items = reviews.data ?? [];
  const run = (item: ReviewItem, decision: ReviewDecision) => {
    setBusyId(item.id);
    decide.mutate({ id: item.id, decision }, { onSettled: () => setBusyId(undefined) });
  };

  return (
    <>
      <PageHeader title={t('review.title')} intro={t('review.intro')} backHref="/graph/" />
      {reviews.isError ? (
        <ErrorNotice
          onRetry={() => {
            void reviews.refetch();
          }}
        />
      ) : null}
      {reviews.isPending ? <Skeleton size="card" /> : null}
      {reviews.isSuccess && items.length === 0 ? (
        <Surface>
          <p>{t('review.empty')}</p>
        </Surface>
      ) : null}
      {items.length > 0 ? (
        <div className={styles.stack} aria-live="polite">
          <p className={styles.label}>{t('review.pending', { count: items.length })}</p>
          {decide.isError ? (
            <p className={styles.danger} role="alert">
              {t('review.failed')}
            </p>
          ) : null}
          <ul className={styles.list}>
            {items.map((item) => (
              <ReviewCard
                key={item.id}
                item={item}
                busy={busyId === item.id}
                onDecide={(d) => run(item, d)}
              />
            ))}
          </ul>
          {items.length >= INBOX_LIMIT ? <p className={styles.muted}>{t('review.more')}</p> : null}
        </div>
      ) : null}
    </>
  );
}
