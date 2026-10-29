import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import type { ReviewDecision, ReviewItem } from '@/data/types';
import { space } from '@/theme/tokens';
import { Button } from '@/ui/Button';
import { Surface } from '@/ui/Surface';
import { Tag } from '@/ui/Tag';
import { Text } from '@/ui/Text';

interface ReviewCardProps {
  item: ReviewItem;
  busy?: boolean;
  onDecide: (decision: ReviewDecision) => void;
  onOpenSource?: () => void;
}

/** One proposal in plain words, what it connects, how sure the system was, and the two ways to answer. */
export function ReviewCard({ item, busy, onDecide, onOpenSource }: ReviewCardProps) {
  const { t } = useTranslation();
  const sentence = t(`graph.relation.${item.kind}`, { from: item.from.label, to: item.to.label });
  return (
    <Surface>
      <Text variant="heading">{sentence}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[2] }}>
        <Tag label={t(`graph.types.${item.from.type}`)} />
        <Tag label={t(`graph.types.${item.to.type}`)} />
        <Tag label={t('review.confidence', { percent: Math.round(item.confidence * 100) })} tone="accent" />
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[3] }}>
        <Button label={t('review.approve')} hint={sentence} disabled={busy} onPress={() => onDecide('approved')} />
        <Button
          variant="secondary"
          label={t('review.reject')}
          hint={sentence}
          disabled={busy}
          onPress={() => onDecide('rejected')}
        />
        {onOpenSource ? (
          <Button variant="ghost" label={t('review.openSource')} disabled={busy} onPress={onOpenSource} />
        ) : null}
      </View>
    </Surface>
  );
}
