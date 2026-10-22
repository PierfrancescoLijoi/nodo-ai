import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useColorScheme } from 'react-native';

import { usePrefs } from '@/prefs/PrefsProvider';

import { darkColors, lightColors, type Colors } from './tokens';

interface ThemeValue {
  colors: Colors;
  scheme: 'light' | 'dark';
}

const ThemeContext = createContext<ThemeValue>({ colors: darkColors, scheme: 'dark' });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const { themePreference } = usePrefs();
  const scheme = themePreference === 'system' ? (system === 'light' ? 'light' : 'dark') : themePreference;
  const value = useMemo<ThemeValue>(
    () => ({ scheme, colors: scheme === 'dark' ? darkColors : lightColors }),
    [scheme],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
