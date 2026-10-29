import { Tabs } from 'expo-router';
import { House, Notebook, Sparkle, TreeStructure } from '@/ui/icons';
import type { Icon } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '@/theme/ThemeProvider';
import { fonts } from '@/theme/tokens';
import { useLayout } from '@/ui/useLayout';

const TABS: { name: string; key: 'home' | 'meetings' | 'ask' | 'graph'; icon: Icon }[] = [
  { name: 'index', key: 'home', icon: House },
  { name: 'meetings', key: 'meetings', icon: Notebook },
  { name: 'ask', key: 'ask', icon: Sparkle },
  { name: 'graph', key: 'graph', icon: TreeStructure },
];

export default function TabsLayout() {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const { bottom } = useSafeAreaInsets();
  const { isDesktop } = useLayout();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarPosition: isDesktop ? 'left' : 'bottom',
        tabBarVariant: isDesktop ? 'material' : 'uikit',
        tabBarStyle: isDesktop
          ? {
              backgroundColor: colors.surface,
              borderRightColor: colors.border,
              width: 232,
              minWidth: 232,
              maxWidth: 232,
              paddingTop: 24,
            }
          : { backgroundColor: colors.surface, borderTopColor: colors.border, height: 72 + bottom, paddingTop: 8 },
        tabBarLabelStyle: isDesktop
          ? { fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 }
          : { fontFamily: fonts.medium, fontSize: 12, lineHeight: 16, marginBottom: 10 },
      }}
    >
      {TABS.map(({ name, key, icon: TabIcon }) => (
        <Tabs.Screen
          key={name}
          name={name}
          options={{
            title: t(`tabs.${key}`),
            tabBarIcon: ({ focused }) => (
              <TabIcon
                size={24}
                color={focused ? colors.accent : colors.textMuted}
                weight={focused ? 'fill' : 'regular'}
              />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}
