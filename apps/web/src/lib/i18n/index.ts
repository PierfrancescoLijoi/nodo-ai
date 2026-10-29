import i18next, { type i18n } from 'i18next';
import { initReactI18next } from 'react-i18next';

import da from './da';
import en from './en';
import it from './it';
import pl from './pl';

export const LOCALES = [
  { code: 'en', nativeName: 'English' },
  { code: 'pl', nativeName: 'Polski' },
  { code: 'it', nativeName: 'Italiano' },
  { code: 'da', nativeName: 'Dansk' },
] as const;

export type Locale = (typeof LOCALES)[number]['code'];
export type Resources = typeof en;

export const isLocale = (value: unknown): value is Locale => LOCALES.some((locale) => locale.code === value);

/** The browser's language when NODO has it, English otherwise. */
export function detectLocale(
  languages: readonly string[] = typeof navigator === 'undefined' ? [] : navigator.languages,
): Locale {
  for (const tag of languages) {
    const code = tag.toLowerCase().split('-')[0];
    if (isLocale(code)) return code;
  }
  return 'en';
}

const resources = { en, pl, it, da };

/** One instance per provider: tests and the app never share language state through a global. */
export function createI18n(locale: Locale): i18n {
  const instance = i18next.createInstance();
  void instance.use(initReactI18next).init({
    resources: Object.fromEntries(
      Object.entries(resources).map(([code, translation]) => [code, { translation }]),
    ),
    lng: locale,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
    initAsync: false,
  });
  return instance;
}

declare module 'i18next' {
  interface CustomTypeOptions {
    resources: { translation: Resources };
  }
}
