'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { I18nextProvider } from 'react-i18next';

import type { RetentionDays } from '@/lib/domain/types';
import { createI18n, detectLocale, isLocale, type Locale } from '@/lib/i18n';
import { localStore, safeGet, safeSet } from '@/lib/runtime/storage';

export type ThemePreference = 'system' | 'light' | 'dark';

export const PREFS_KEY = 'nodo.prefs';
const THEMES: readonly ThemePreference[] = ['system', 'light', 'dark'];
const RETENTION: readonly RetentionDays[] = [7, 30, 90];

interface Stored {
  locale: Locale;
  theme: ThemePreference;
  retentionDays: RetentionDays;
}

interface Prefs extends Stored {
  /** Browser state has been read (false in the static HTML and during hydration). */
  ready: boolean;
  setLocale(locale: Locale): void;
  setTheme(theme: ThemePreference): void;
  setRetentionDays(days: RetentionDays): void;
}

const PrefsContext = createContext<Prefs | null>(null);

export function readStored(raw: string | null, fallbackLocale: Locale): Stored {
  const defaults: Stored = { locale: fallbackLocale, theme: 'system', retentionDays: 30 };
  if (!raw) return defaults;
  try {
    const value = JSON.parse(raw) as Partial<Stored>;
    return {
      locale: isLocale(value.locale) ? value.locale : defaults.locale,
      theme: THEMES.includes(value.theme as ThemePreference)
        ? (value.theme as ThemePreference)
        : defaults.theme,
      retentionDays: RETENTION.includes(value.retentionDays as RetentionDays)
        ? (value.retentionDays as RetentionDays)
        : defaults.retentionDays,
    };
  } catch {
    return defaults;
  }
}

// The choices live in localStorage (kept in memory when storage is refused). React reads them as an
// external store, so there is no "load on mount" state to keep in step.
const listeners = new Set<() => void>();
let memory: string | null = null;
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  window.addEventListener('storage', listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', listener);
  };
};
const snapshot = () => safeGet(localStore(), PREFS_KEY) ?? memory;
const serverSnapshot = () => null;
const write = (raw: string) => {
  memory = raw;
  safeSet(localStore(), PREFS_KEY, raw);
  listeners.forEach((listener) => listener());
};

const noSubscription = () => () => undefined;

function applyToDocument({ locale, theme }: Stored): void {
  const root = document.documentElement;
  root.lang = locale;
  if (theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const raw = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const ready = useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
  const stored = useMemo(() => readStored(raw, ready ? detectLocale() : 'en'), [raw, ready]);
  const [i18n] = useState(() => createI18n('en'));

  useEffect(() => {
    void i18n.changeLanguage(stored.locale);
    applyToDocument(stored);
  }, [i18n, stored]);

  const update = useCallback(
    (patch: Partial<Stored>) => write(JSON.stringify({ ...stored, ...patch })),
    [stored],
  );

  const value = useMemo<Prefs>(
    () => ({
      ...stored,
      ready,
      setLocale: (locale) => update({ locale }),
      setTheme: (theme) => update({ theme }),
      setRetentionDays: (retentionDays) => update({ retentionDays }),
    }),
    [ready, stored, update],
  );

  return (
    <PrefsContext.Provider value={value}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </PrefsContext.Provider>
  );
}

export function usePrefs(): Prefs {
  const value = useContext(PrefsContext);
  if (!value) throw new Error('usePrefs must be used inside PrefsProvider');
  return value;
}
