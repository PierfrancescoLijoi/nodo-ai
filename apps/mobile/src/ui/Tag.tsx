import { View } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius, space } from '@/theme/tokens';

import { Text } from './Text';

export function Tag({ label, tone = 'default' }: { label: string; tone?: 'default' | 'accent' }) {
  const { colors } = useTheme();
  return (
    <View
      style={{
        alignSelf: 'flex-start',
        borderRadius: radius.pill,
        paddingHorizontal: space[3],
        paddingVertical: space[1],
        backgroundColor: tone === 'accent' ? colors.accentSoft : colors.surfaceRaised,
      }}
    >
      <Text variant="label" tone={tone === 'accent' ? 'accent' : 'muted'}>
        {label}
      </Text>
    </View>
  );
}
