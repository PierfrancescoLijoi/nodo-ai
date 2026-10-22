import { useRouter } from 'expo-router';
import { Gear } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { Pressable } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { radius, touch } from '@/theme/tokens';

export function SettingsButton() {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={t('common.settings')}
      onPress={() => router.push('/settings')}
      style={({ pressed }) => ({
        width: touch,
        height: touch,
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: radius.control,
        backgroundColor: pressed ? colors.surfaceRaised : 'transparent',
        marginRight: -8,
      })}
    >
      <Gear size={24} color={colors.text} weight="regular" />
    </Pressable>
  );
}
