import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useAsync, useRepository } from '@/data/RepositoryProvider';
import type { ReviewDecision, ReviewItem } from '@/data/types';
import { space } from '@/theme/tokens';
import { ErrorNotice } from '@/ui/ErrorNotice';
import { Screen } from '@/ui/Screen';
import { Skeleton } from '@/ui/Skeleton';
import { Surface } from '@/ui/Surface';
import { Text } from '@/ui/Text';

import { ReviewCard } from './ReviewCard';

/** The inbox: what the system proposed and a person has not yet decided (ADR-004, ADR-025). */
export default function ReviewScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const repository = useRepository();
  const reviews = useAsync((r) => r.listReviews(), []);
  const [decided, setDecided] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<string>();
  const [failed, setFailed] = useState(false);

  const decide = async (item: ReviewItem, decision: ReviewDecision) => {
    setBusy(item.id);
    setFailed(false);
    try {
      await repository.reviewRelation(item.id, decision);
      setDecided((current) => new Set(current).add(item.id));
    } catch {
      setFailed(true);
    } finally {
      setBusy(undefined);
    }
  };

  const waiting = reviews.status === 'ready' ? reviews.data.filter((item) => !decided.has(item.id)) : [];

  return (
    <Screen title={t('review.title')} intro={t('review.intro')} back>
      {reviews.status === 'error' ? (
        <ErrorNotice onRetry={reviews.retry} />
      ) : reviews.status === 'loading' ? (
        <Skeleton height={160} />
      ) : waiting.length === 0 ? (
        <Surface>
          <Text>{t('review.empty')}</Text>
        </Surface>
      ) : (
        <View style={{ gap: space[4] }} accessibilityLiveRegion="polite">
          <Text variant="label" tone="muted">
            {t('review.pending', { count: waiting.length })}
          </Text>
          {failed ? <Text tone="danger">{t('review.failed')}</Text> : null}
          {waiting.map((item) => (
            <ReviewCard
              key={item.id}
              item={item}
              busy={busy === item.id}
              onDecide={(decision) => void decide(item, decision)}
              onOpenSource={
                item.meetingId
                  ? () => router.push({ pathname: '/meeting/[id]', params: { id: item.meetingId as string } })
                  : undefined
              }
            />
          ))}
          {reviews.data.length >= 100 ? <Text tone="muted">{t('review.more')}</Text> : null}
        </View>
      )}
    </Screen>
  );
}
