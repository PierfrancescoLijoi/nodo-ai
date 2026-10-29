import type { Locale } from './index';

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function formatDate(iso: string, locale: Locale, style: 'short' | 'long' = 'short'): string {
  const options: Intl.DateTimeFormatOptions =
    style === 'long' ? { day: 'numeric', month: 'long', year: 'numeric' } : { day: 'numeric', month: 'short' };
  // A date without a time is a calendar day, not an instant: keep it in UTC so no zone shifts it.
  if (DATE_ONLY.test(iso)) options.timeZone = 'UTC';
  return new Intl.DateTimeFormat(locale, options).format(new Date(iso));
}

// Case and diacritic insensitive form for search ("lodz" finds "Łódź", "orsted" finds "Ørsted").
const LETTER_MAP: Record<string, string> = { ł: 'l', ø: 'o', æ: 'ae', đ: 'd', ß: 'ss' };

export function foldText(text: string, locale: Locale): string {
  return text
    .toLocaleLowerCase(locale)
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/[łøæđß]/g, (letter) => LETTER_MAP[letter] ?? letter);
}

export function formatTime(iso: string, locale: Locale): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
}

export function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = String(Math.floor(safe / 60)).padStart(2, '0');
  const seconds = String(safe % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}
