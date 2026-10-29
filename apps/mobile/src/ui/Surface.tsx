import type { ReactNode } from 'react';
import { View, type ViewStyle } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius, space } from '@/theme/tokens';

interface SurfaceProps {
  children: ReactNode;
  tone?: 'default' | 'accent' | 'warning';
  style?: ViewStyle;
}

export function Surface({ children, tone = 'default', style }: SurfaceProps) {
  const { colors } = useTheme();
  const background = { default: colors.surface, accent: colors.accentSoft, warning: colors.warningSoft }[tone];
  return (
    <View
      style={[
        {
          backgroundColor: background,
          borderRadius: radius.card,
          borderWidth: 1,
          borderColor: tone === 'default' ? colors.border : background,
          padding: space[4],
          gap: space[3],
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}
