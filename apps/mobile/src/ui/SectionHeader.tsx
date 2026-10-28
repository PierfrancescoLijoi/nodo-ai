import { Pressable, View } from 'react-native';

import { touch } from '@/theme/tokens';

import { Text } from './Text';

interface SectionHeaderProps {
  title: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function SectionHeader({ title, actionLabel, onAction }: SectionHeaderProps) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 32 }}>
      <Text variant="heading" accessibilityRole="header">
        {title}
      </Text>
      {actionLabel && onAction ? (
        <Pressable
          accessibilityRole="link"
          onPress={onAction}
          style={{ minHeight: touch, justifyContent: 'center', paddingLeft: 12, marginVertical: -8 }}
        >
          <Text variant="label" tone="accent">
            {actionLabel}
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}
