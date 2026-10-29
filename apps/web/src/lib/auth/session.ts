import { safeGet, safeRemove, safeSet, type StorageLike } from '../runtime/storage';
import type { Tokens } from './oidc';

const TOKENS_KEY = 'nodo.auth';

/**
 * Tokens live in the tab's session storage: gone when the tab closes, never shared with other tabs or
 * written to disk. The page's content policy (no inline script that is not hashed, no eval, same-origin
 * scripts only) is what keeps script injected from elsewhere away from them.
 */
export interface TokenStore {
  get(): Tokens | undefined;
  set(tokens: Tokens): void;
  clear(): void;
}

export function sessionTokenStore(storage: StorageLike | undefined): TokenStore {
  return {
    get() {
      const raw = safeGet(storage, TOKENS_KEY);
      if (!raw) return undefined;
      try {
        const value = JSON.parse(raw) as Partial<Tokens>;
        if (typeof value.accessToken !== 'string' || typeof value.expiresAt !== 'number') return undefined;
        return {
          accessToken: value.accessToken,
          expiresAt: value.expiresAt,
          ...(typeof value.refreshToken === 'string' ? { refreshToken: value.refreshToken } : {}),
        };
      } catch {
        return undefined;
      }
    },
    set: (tokens) => void safeSet(storage, TOKENS_KEY, JSON.stringify(tokens)),
    clear: () => safeRemove(storage, TOKENS_KEY),
  };
}

interface SessionOptions {
  store: TokenStore;
  now: () => number;
  refresh: (refreshToken: string) => Promise<Tokens>;
  onSignedOut: () => void;
}

/** A token is renewed when less than this is left, so a request never leaves with one about to lapse. */
export const EXPIRY_MARGIN_MS = 60_000;

export function createSession({ store, now, refresh, onSignedOut }: SessionOptions) {
  let inFlight: Promise<string> | undefined;

  const signOut = () => {
    store.clear();
    onSignedOut();
  };

  const renew = async (tokens: Tokens): Promise<string> => {
    if (!tokens.refreshToken) {
      signOut();
      throw new Error('No refresh token is available');
    }
    try {
      const next = await refresh(tokens.refreshToken);
      store.set(next);
      return next.accessToken;
    } catch (error) {
      signOut();
      throw error;
    }
  };

  // Parallel requests share one renewal: a refresh token may be single-use.
  const getAccessToken = async (forceRefresh = false): Promise<string> => {
    const tokens = store.get();
    if (!tokens) throw new Error('No signed-in session');
    if (!forceRefresh && tokens.expiresAt - now() >= EXPIRY_MARGIN_MS) return tokens.accessToken;
    inFlight ??= renew(tokens).finally(() => {
      inFlight = undefined;
    });
    return inFlight;
  };

  return { getAccessToken, signOut };
}
