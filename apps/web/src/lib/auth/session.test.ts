import { describe, expect, it, vi } from 'vitest';

import type { Tokens } from './oidc';
import { createSession, EXPIRY_MARGIN_MS, sessionTokenStore, type TokenStore } from './session';

function memoryStore(initial?: Tokens): TokenStore & { value?: Tokens } {
  const store: TokenStore & { value?: Tokens } = {
    value: initial,
    get: () => store.value,
    set: (tokens) => void (store.value = tokens),
    clear: () => void (store.value = undefined),
  };
  return store;
}

const NOW = 1_000_000;
const valid = (extra: Partial<Tokens> = {}): Tokens => ({
  accessToken: 'a1',
  refreshToken: 'r1',
  expiresAt: NOW + 10 * EXPIRY_MARGIN_MS,
  ...extra,
});

describe('createSession', () => {
  it('returns a token that is not about to expire without touching the provider', async () => {
    const refresh = vi.fn();
    const session = createSession({
      store: memoryStore(valid()),
      now: () => NOW,
      refresh,
      onSignedOut: vi.fn(),
    });
    await expect(session.getAccessToken()).resolves.toBe('a1');
    expect(refresh).not.toHaveBeenCalled();
  });

  it('renews a token inside the safety margin and keeps the new one', async () => {
    const store = memoryStore(valid({ expiresAt: NOW + EXPIRY_MARGIN_MS - 1 }));
    const refresh = vi.fn(async () => valid({ accessToken: 'a2', refreshToken: 'r2' }));
    const session = createSession({ store, now: () => NOW, refresh, onSignedOut: vi.fn() });
    await expect(session.getAccessToken()).resolves.toBe('a2');
    expect(refresh).toHaveBeenCalledWith('r1');
    expect(store.value?.refreshToken).toBe('r2');
  });

  it('renews on demand, and parallel requests share one renewal (a refresh token may be single use)', async () => {
    let release: (tokens: Tokens) => void = () => undefined;
    const refresh = vi.fn(() => new Promise<Tokens>((resolve) => (release = resolve)));
    const session = createSession({
      store: memoryStore(valid()),
      now: () => NOW,
      refresh,
      onSignedOut: vi.fn(),
    });
    const first = session.getAccessToken(true);
    const second = session.getAccessToken(true);
    release(valid({ accessToken: 'a2' }));
    await expect(Promise.all([first, second])).resolves.toEqual(['a2', 'a2']);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('signs out when the provider refuses the renewal', async () => {
    const store = memoryStore(valid({ expiresAt: NOW }));
    const onSignedOut = vi.fn();
    const session = createSession({
      store,
      now: () => NOW,
      refresh: async () => {
        throw new Error('invalid_grant');
      },
      onSignedOut,
    });
    await expect(session.getAccessToken()).rejects.toThrow('invalid_grant');
    expect(onSignedOut).toHaveBeenCalledTimes(1);
    expect(store.value).toBeUndefined();
  });

  it('signs out when an expired session has no refresh token', async () => {
    const store = memoryStore({ accessToken: 'a', expiresAt: NOW });
    const onSignedOut = vi.fn();
    const session = createSession({ store, now: () => NOW, refresh: vi.fn(), onSignedOut });
    await expect(session.getAccessToken()).rejects.toThrow('No refresh token');
    expect(onSignedOut).toHaveBeenCalled();
  });

  it('refuses to hand out a token when nobody is signed in', async () => {
    const session = createSession({
      store: memoryStore(),
      now: () => NOW,
      refresh: vi.fn(),
      onSignedOut: vi.fn(),
    });
    await expect(session.getAccessToken()).rejects.toThrow('No signed-in session');
  });

  it('signOut clears the tokens and tells the app', () => {
    const store = memoryStore(valid());
    const onSignedOut = vi.fn();
    createSession({ store, now: () => NOW, refresh: vi.fn(), onSignedOut }).signOut();
    expect(store.value).toBeUndefined();
    expect(onSignedOut).toHaveBeenCalled();
  });
});

describe('sessionTokenStore', () => {
  const storage = () => {
    const map = new Map<string, string>();
    return {
      map,
      getItem: (key: string) => map.get(key) ?? null,
      setItem: (key: string, value: string) => void map.set(key, value),
      removeItem: (key: string) => void map.delete(key),
    };
  };

  it('round-trips tokens through storage and forgets them on clear', () => {
    const backing = storage();
    const store = sessionTokenStore(backing);
    expect(store.get()).toBeUndefined();
    store.set(valid());
    expect(sessionTokenStore(backing).get()).toEqual(valid());
    store.clear();
    expect(store.get()).toBeUndefined();
  });

  it('treats damaged or foreign content as no session', () => {
    const backing = storage();
    backing.map.set('nodo.auth', 'not json');
    expect(sessionTokenStore(backing).get()).toBeUndefined();
    backing.map.set('nodo.auth', JSON.stringify({ accessToken: 1 }));
    expect(sessionTokenStore(backing).get()).toBeUndefined();
  });

  it('works without storage (the session then lasts only as long as the page)', () => {
    const store = sessionTokenStore(undefined);
    store.set(valid());
    expect(store.get()).toBeUndefined();
  });
});
