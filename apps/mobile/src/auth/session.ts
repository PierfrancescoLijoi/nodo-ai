export interface StoredTokens {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
}

export interface TokenStore {
  get(): Promise<StoredTokens | undefined>;
  set(tokens: StoredTokens): Promise<void>;
  clear(): Promise<void>;
}

interface SessionOptions {
  store: TokenStore;
  now: () => number;
  refresh: (refreshToken: string) => Promise<StoredTokens>;
  onSignedOut: () => void;
}

export function createSession({ store, now, refresh, onSignedOut }: SessionOptions) {
  let inFlight: Promise<string> | undefined;

  const signOut = async () => {
    await store.clear();
    onSignedOut();
  };

  const refreshAccessToken = async (tokens: StoredTokens): Promise<string> => {
    if (!tokens.refreshToken) {
      await signOut();
      throw new Error('No refresh token is available');
    }
    try {
      const next = await refresh(tokens.refreshToken);
      await store.set(next);
      return next.accessToken;
    } catch (error) {
      await signOut();
      throw error;
    }
  };

  const getAccessToken = async (forceRefresh = false): Promise<string> => {
    const tokens = await store.get();
    if (!tokens) throw new Error('No signed-in session');
    if (!forceRefresh && tokens.expiresAt - now() >= 60_000) return tokens.accessToken;
    if (!inFlight) inFlight = refreshAccessToken(tokens).finally(() => (inFlight = undefined));
    return inFlight;
  };

  return { getAccessToken, signOut };
}
