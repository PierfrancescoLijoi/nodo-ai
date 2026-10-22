import { createSession, type StoredTokens } from '../session';

function memoryStore(initial?: StoredTokens) {
  let value = initial;
  return {
    get: jest.fn(async () => value),
    set: jest.fn(async (next: StoredTokens) => {
      value = next;
    }),
    clear: jest.fn(async () => {
      value = undefined;
    }),
  };
}

describe('session', () => {
  const valid = { accessToken: 'access', refreshToken: 'refresh', expiresAt: 200_000 };

  it('returns a token with more than 60 seconds remaining', async () => {
    const store = memoryStore(valid);
    const refresh = jest.fn();
    const session = createSession({ store, now: () => 100_000, refresh, onSignedOut: jest.fn() });
    await expect(session.getAccessToken()).resolves.toBe('access');
    expect(refresh).not.toHaveBeenCalled();
  });

  it('shares a single refresh between concurrent callers', async () => {
    const store = memoryStore({ ...valid, expiresAt: 150_000 });
    const refresh = jest.fn(async () => ({ accessToken: 'new', refreshToken: 'new-refresh', expiresAt: 300_000 }));
    const session = createSession({ store, now: () => 100_000, refresh, onSignedOut: jest.fn() });
    await expect(Promise.all([session.getAccessToken(), session.getAccessToken()])).resolves.toEqual(['new', 'new']);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('clears tokens and signs out when refresh fails', async () => {
    const store = memoryStore({ ...valid, expiresAt: 150_000 });
    const onSignedOut = jest.fn();
    const session = createSession({
      store,
      now: () => 100_000,
      refresh: async () => {
        throw new Error('failed');
      },
      onSignedOut,
    });
    await expect(session.getAccessToken()).rejects.toThrow('failed');
    expect(store.clear).toHaveBeenCalled();
    expect(onSignedOut).toHaveBeenCalled();
  });
});
