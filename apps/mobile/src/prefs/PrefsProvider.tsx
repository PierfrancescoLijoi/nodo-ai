import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import i18n, { detectLocale, type Locale } from '@/i18n';

export type ThemePreference = 'system' | 'light' | 'dark';
export type RetentionDays = 7 | 30 | 90;

interface Prefs {
  locale: Locale;
  themePreference: ThemePreference;
  retentionDays: RetentionDays;
  setLocale: (locale: Locale) => void;
  setThemePreference: (preference: ThemePreference) => void;
  setRetentionDays: (days: RetentionDays) => void;
}

const PrefsContext = createContext<Prefs | null>(null);

// ponytail: in-memory only, add expo-secure-store persistence once accounts exist.
export function PrefsProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale);
  const [themePreference, setThemePreference] = useState<ThemePreference>('system');
  const [retentionDays, setRetentionDays] = useState<RetentionDays>(30);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    void i18n.changeLanguage(next);
  }, []);

  const value = useMemo(
    () => ({ locale, themePreference, retentionDays, setLocale, setThemePreference, setRetentionDays }),
    [locale, themePreference, retentionDays, setLocale],
  );
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): Prefs {
  const value = useContext(PrefsContext);
  if (!value) throw new Error('usePrefs must be used inside PrefsProvider');
  return value;
}
