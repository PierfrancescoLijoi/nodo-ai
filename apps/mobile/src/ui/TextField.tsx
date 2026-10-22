import { useState } from 'react';
import { TextInput, View, type TextInputProps } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { fonts, radius, space, touch } from '@/theme/tokens';

import { Text } from './Text';

interface TextFieldProps extends Omit<TextInputProps, 'style'> {
  label: string;
  hideLabel?: boolean;
}

export function TextField({ label, hideLabel, ...rest }: TextFieldProps) {
  const { colors } = useTheme();
  const [focused, setFocused] = useState(false);
  return (
    <View style={{ gap: space[2] }}>
      {hideLabel ? null : <Text variant="label">{label}</Text>}
      <TextInput
        {...rest}
        accessibilityLabel={label}
        placeholderTextColor={colors.textMuted}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          minHeight: touch,
          paddingHorizontal: space[4],
          borderRadius: radius.control,
          borderWidth: focused ? 2 : 1,
          borderColor: focused ? colors.accent : colors.borderStrong,
          backgroundColor: colors.surface,
          color: colors.text,
          fontFamily: fonts.regular,
          fontSize: 15,
        }}
      />
    </View>
  );
}
