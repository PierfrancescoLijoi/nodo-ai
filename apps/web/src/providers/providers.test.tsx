import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api/client';
import { safeSet, localStore } from '@/lib/runtime/storage';
import { shouldRetry } from './data';
import { PREFS_KEY, PrefsProvider, readStored, usePrefs } from './prefs';

const wrapper = ({ children }: { children: ReactNode }) => <PrefsProvider>{children}</PrefsProvider>;

describe('readStored', () => {
  it('uses the defaults for nothing, and for damaged or out-of-range content', () => {
    const defaults = { locale: 'pl', theme: 'system', retentionDays: 30 };
    expect(readStored(null, 'pl')).toEqual(defaults);
    expect(readStored('not json', 'pl')).toEqual(defaults);
    expect(readStored(JSON.stringify({ locale: 'xx', theme: 'neon', retentionDays: 3 }), 'pl')).toEqual(
      defaults,
    );
  });

  it('keeps what was chosen', () => {
    expect(readStored(JSON.stringify({ locale: 'da', theme: 'dark', retentionDays: 7 }), 'en')).toEqual({
      locale: 'da',
      theme: 'dark',
      retentionDays: 7,
    });
  });
});

describe('PrefsProvider', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.lang = 'en';
  });

  it('stores a choice and applies it to the page', () => {
    const { result } = renderHook(() => usePrefs(), { wrapper });
    expect(result.current.ready).toBe(true);
    act(() => result.current.setTheme('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => result.current.setLocale('it'));
    expect(document.documentElement.lang).toBe('it');
    act(() => result.current.setRetentionDays(90));
    expect(JSON.parse(window.localStorage.getItem(PREFS_KEY) ?? '{}')).toEqual({
      locale: 'it',
      theme: 'dark',
      retentionDays: 90,
    });
    act(() => result.current.setTheme('system'));
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it('starts from what an earlier visit stored', () => {
    safeSet(localStore(), PREFS_KEY, JSON.stringify({ locale: 'pl', theme: 'light', retentionDays: 7 }));
    const { result } = renderHook(() => usePrefs(), { wrapper });
    expect(result.current).toMatchObject({ locale: 'pl', theme: 'light', retentionDays: 7 });
  });
});

describe('shouldRetry', () => {
  it('retries server trouble and dropped connections, a few times', () => {
    expect(shouldRetry(0, new ApiError('x', 503, 'unavailable'))).toBe(true);
    expect(shouldRetry(1, new ApiError('x', 0, 'network_error'))).toBe(true);
    expect(shouldRetry(2, new ApiError('x', 503, 'unavailable'))).toBe(false);
  });

  it('does not retry a refusal, a cancellation or an unknown error', () => {
    expect(shouldRetry(0, new ApiError('x', 404, 'not_found'))).toBe(false);
    expect(shouldRetry(0, new ApiError('x', 422, 'validation_error'))).toBe(false);
    expect(shouldRetry(0, new ApiError('x', 0, 'cancelled'))).toBe(false);
    expect(shouldRetry(0, new Error('bug'))).toBe(false);
  });
});
