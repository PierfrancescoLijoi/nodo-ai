import type { Icon } from 'phosphor-react-native';
import { Pressable, View } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';

import { Text } from './Text';

type Variant = 'primary' | 'secondary' | 'ghost';

interface ButtonProps {
  label: string;
  onPress: () => void;
  variant?: Variant;
  icon?: Icon;
  disabled?: boolean;
  hint?: string;
  fullWidth?: boolean;
}

export function Button({
  label,
  onPress,
  variant = 'primary',
  icon: IconComponent,
  disabled,
  hint,
  fullWidth,
}: ButtonProps) {
  const { colors } = useTheme();
  const background = { primary: colors.accent, secondary: colors.surfaceRaised, ghost: 'transparent' }[variant];
  const tone = variant === 'primary' ? 'onAccent' : variant === 'ghost' ? 'accent' : 'default';
  const iconColor = variant === 'primary' ? colors.accentText : variant === 'ghost' ? colors.accent : colors.text;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={hint}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => ({
        minHeight: touch,
        paddingHorizontal: space[5],
        borderRadius: radius.control,
        backgroundColor: background,
        borderWidth: variant === 'secondary' ? 1 : 0,
        borderColor: colors.border,
        alignItems: 'center',
        justifyContent: 'center',
        alignSelf: fullWidth ? 'stretch' : 'flex-start',
        opacity: disabled ? 0.4 : 1,
        transform: [{ scale: pressed ? 0.98 : 1 }],
      })}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[2] }}>
        {IconComponent ? <IconComponent size={20} color={iconColor} weight="bold" /> : null}
        <Text variant="heading" tone={tone}>
          {label}
        </Text>
      </View>
    </Pressable>
  );
}
