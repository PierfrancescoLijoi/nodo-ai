import { getLocales } from 'expo-localization';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import da from './locales/da';
import en from './locales/en';
import it from './locales/it';
import pl from './locales/pl';

export const LOCALES = [
  { code: 'en', nativeName: 'English' },
  { code: 'pl', nativeName: 'Polski' },
  { code: 'it', nativeName: 'Italiano' },
  { code: 'da', nativeName: 'Dansk' },
] as const;

export type Locale = (typeof LOCALES)[number]['code'];

export const isLocale = (value: unknown): value is Locale => LOCALES.some((l) => l.code === value);

export function detectLocale(): Locale {
  const code = getLocales()[0]?.languageCode;
  return isLocale(code) ? code : 'en';
}

// eslint-disable-next-line import/no-named-as-default-member
void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    pl: { translation: pl },
    it: { translation: it },
    da: { translation: da },
  },
  lng: detectLocale(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
  initAsync: false,
});

export default i18n;
