import { Warning } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { View } from 'react-native';

import { useTheme } from '@/theme/ThemeProvider';
import { space } from '@/theme/tokens';

import { Surface } from './Surface';
import { Text } from './Text';

export function ErrorNotice() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  return (
    <Surface tone="warning">
      <View accessibilityRole="alert" style={{ flexDirection: 'row', gap: space[3], alignItems: 'center' }}>
        <Warning size={20} color={colors.warning} weight="fill" />
        <Text style={{ flex: 1 }}>{t('common.loadError')}</Text>
      </View>
    </Surface>
  );
}
