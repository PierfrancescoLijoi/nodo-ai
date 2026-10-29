import { Text as NativeText, type TextProps as NativeTextProps } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { type, type TypeVariant } from '@/theme/tokens';

export type Tone = 'default' | 'muted' | 'accent' | 'warning' | 'danger' | 'onAccent';

interface TextProps extends NativeTextProps {
  variant?: TypeVariant;
  tone?: Tone;
}

export function Text({ variant = 'body', tone = 'default', style, ...rest }: TextProps) {
  const { colors } = useTheme();
  const color = {
    default: colors.text,
    muted: colors.textMuted,
    accent: colors.accent,
    warning: colors.warning,
    danger: colors.danger,
    onAccent: colors.accentText,
  }[tone];
  return <NativeText {...rest} style={[type[variant], { color }, style]} />;
}
