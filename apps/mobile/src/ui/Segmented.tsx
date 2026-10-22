import { Pressable, View } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius, space, touch } from '@/theme/tokens';

import { Text } from './Text';

interface Option<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
}

export function Segmented<T extends string>({ label, options, value, onChange }: SegmentedProps<T>) {
  const { colors } = useTheme();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      style={{
        flexDirection: 'row',
        backgroundColor: colors.surfaceRaised,
        borderRadius: radius.control,
        padding: space[1],
        gap: space[1],
      }}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            onPress={() => onChange(option.value)}
            style={{
              flex: 1,
              minHeight: touch - 8,
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: radius.control - 4,
              backgroundColor: selected ? colors.surface : 'transparent',
              borderWidth: selected ? 1 : 0,
              borderColor: colors.border,
            }}
          >
            <Text variant="label" tone={selected ? 'default' : 'muted'}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
