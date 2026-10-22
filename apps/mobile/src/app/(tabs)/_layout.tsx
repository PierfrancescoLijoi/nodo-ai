import { Tabs } from 'expo-router';
import { House, Notebook, Sparkle, TreeStructure, type Icon } from 'phosphor-react-native';
import { useTranslation } from 'react-i18next';

import { useTheme } from '@/theme/ThemeProvider';
import { fonts } from '@/theme/tokens';

const TABS: { name: string; key: 'home' | 'meetings' | 'ask' | 'graph'; icon: Icon }[] = [
  { name: 'index', key: 'home', icon: House },
  { name: 'meetings', key: 'meetings', icon: Notebook },
  { name: 'ask', key: 'ask', icon: Sparkle },
  { name: 'graph', key: 'graph', icon: TreeStructure },
];

export default function TabsLayout() {
  const { colors } = useTheme();
  const { t } = useTranslation();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border, height: 72, paddingTop: 8 },
        tabBarLabelStyle: { fontFamily: fonts.medium, fontSize: 12, lineHeight: 16, marginBottom: 10 },
      }}
    >
      {TABS.map(({ name, key, icon: TabIcon }) => (
        <Tabs.Screen
          key={name}
          name={name}
          options={{
            title: t(`tabs.${key}`),
            tabBarIcon: ({ focused }) => (
              <TabIcon size={24} color={focused ? colors.accent : colors.textMuted} weight={focused ? 'fill' : 'regular'} />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}
