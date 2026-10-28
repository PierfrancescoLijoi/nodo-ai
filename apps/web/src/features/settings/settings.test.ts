import { describe, expect, it } from 'vitest';

import { mapIndex, mapSync } from '@/lib/api/local';
import { safeReturnTo } from '../auth/CallbackScreen';
import { missingKey } from './EngineSection';
import { isSyncTokenMessage, normalizeTarget, readTarget, saveTarget, SYNC_TARGET_KEY } from './syncTarget';

describe('normalizeTarget', () => {
  const good = {
    apiBaseUrl: 'https://api.example/',
    issuer: 'https://login.example/t/v2.0/',
    clientId: ' c ',
    scope: ' api://nodo/access ',
  };

  it('trims, drops trailing slashes and accepts complete https settings', () => {
    expect(normalizeTarget(good)).toEqual({
      apiBaseUrl: 'https://api.example',
      issuer: 'https://login.example/t/v2.0',
      clientId: 'c',
      scope: 'api://nodo/access',
    });
  });

  it.each([
    ['plain http', { ...good, apiBaseUrl: 'http://api.example' }],
    ['a script address', { ...good, issuer: 'javascript:alert(1)' }],
    ['no client', { ...good, clientId: '' }],
    ['no scope', { ...good, scope: '  ' }],
    ['nothing', {}],
  ])('refuses %s', (_name, target) => expect(normalizeTarget(target)).toBeUndefined());
});

describe('the remembered target', () => {
  it('round-trips and survives damaged storage', () => {
    const map = new Map<string, string>();
    const storage = {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
    };
    saveTarget(storage, { apiBaseUrl: 'https://a', issuer: 'https://i', clientId: 'c', scope: 's' });
    expect(readTarget(storage).clientId).toBe('c');
    map.set(SYNC_TARGET_KEY, '{broken');
    expect(readTarget(storage)).toEqual({ apiBaseUrl: '', issuer: '', clientId: '', scope: '' });
  });
});

describe('isSyncTokenMessage', () => {
  it('accepts only the token message', () => {
    expect(isSyncTokenMessage({ type: 'nodo:sync-token', token: 't' })).toBe(true);
    expect(isSyncTokenMessage({ type: 'other', token: 't' })).toBe(false);
    expect(isSyncTokenMessage({ type: 'nodo:sync-token', token: 5 })).toBe(false);
    expect(isSyncTokenMessage(null)).toBe(false);
  });
});

describe('safeReturnTo', () => {
  it.each([
    ['/meetings/', '/meetings/'],
    ['/', '/'],
    ['//evil.example', '/'],
    ['https://evil.example', '/'],
    ['/\\evil.example', '/'],
    ['javascript:alert(1)', '/'],
    ['', '/'],
  ])('%j -> %s', (value, expected) => expect(safeReturnTo(value)).toBe(expected));
});

describe('local engine mapping', () => {
  it('maps a sync report and calls an unknown state a failure', () => {
    const report = {
      state: 'done',
      total: 3,
      done: 3,
      pushed: 2,
      unchanged: 1,
      deleted: 0,
      failed: [{ meeting_id: 'm', reason: 'r' }],
      message: '',
    };
    expect(mapSync(report)).toMatchObject({ copied: 2, failed: [{ meetingId: 'm', reason: 'r' }] });
    expect(() => mapSync({ ...report, state: 'weird' })).toThrow();
  });

  it('maps an index report, naming differences it does not know "other"', () => {
    const report = {
      state: 'done',
      mode: 'verify',
      checked: 4,
      skipped: 0,
      repaired: 0,
      findings: [
        { kind: 'stale_index', meeting_id: 'a' },
        { kind: 'brand_new_kind', meeting_id: 'b' },
      ],
      failed: [],
      message: '',
    };
    expect(mapIndex(report).findings).toEqual([
      { kind: 'stale_index', meetingId: 'a' },
      { kind: 'other', meetingId: 'b' },
    ]);
    expect(mapIndex({ ...report, mode: '' }).mode).toBe('');
  });

  it('sorts missing models into the messages the screen has', () => {
    expect(missingKey('language model (GGUF)')).toBe('language');
    expect(missingKey('faster-whisper medium')).toBe('speech');
    expect(missingKey('multilingual E5 embedding')).toBe('embedding');
    expect(missingKey('ffmpeg')).toBe('other');
  });
});
