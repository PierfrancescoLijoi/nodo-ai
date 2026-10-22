import type { Locale } from './index';

export function formatDate(iso: string, locale: Locale, style: 'short' | 'long' = 'short'): string {
  const options: Intl.DateTimeFormatOptions =
    style === 'long'
      ? { day: 'numeric', month: 'long', year: 'numeric' }
      : { day: 'numeric', month: 'short' };
  return new Intl.DateTimeFormat(locale, options).format(new Date(iso));
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
