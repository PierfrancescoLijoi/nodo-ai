import { describe, expect, it, vi } from 'vitest';

import { cloudConfigSchema, ConfigError, isAllowedUrl, loadCloudConfig } from './config';
import { LOCAL_TOKEN_KEY, resolveConnection, type BrowserContext } from './connection';
import { safeGet, safeRemove, safeSet, type StorageLike } from './storage';

const storage = (): StorageLike & { map: Map<string, string> } => {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
  };
};

const browser = (over: Partial<BrowserContext> = {}): BrowserContext & { replaced: string[] } => {
  const replaced: string[] = [];
  return {
    origin: 'https://nodo.example',
    pathname: '/',
    search: '',
    hash: '',
    session: storage(),
    replaceUrl: (url) => void replaced.push(url),
    replaced,
    ...over,
  };
};

const CLOUD = {
  mode: 'cloud' as const,
  apiBaseUrl: '',
  oidc: { issuer: 'https://tenant.ciamlogin.com/t/v2.0', clientId: 'c', scope: 'api://nodo/access' },
};

describe('isAllowedUrl', () => {
  it.each([
    ['https://login.example/v2.0', true],
    ['http://localhost:8765', true],
    ['http://127.0.0.1:8798/idp', true],
    ['http://example.com', false],
    ['javascript:alert(1)', false],
    ['ftp://x', false],
    ['not a url', false],
  ])('%s -> %s', (value, expected) => expect(isAllowedUrl(value)).toBe(expected));
});

describe('cloudConfigSchema', () => {
  it('accepts a deployment and defaults the API to this origin', () => {
    expect(cloudConfigSchema.parse(CLOUD).apiBaseUrl).toBe('');
  });

  it('refuses an insecure identity provider and incomplete settings', () => {
    expect(
      cloudConfigSchema.safeParse({ ...CLOUD, oidc: { ...CLOUD.oidc, issuer: 'http://idp.example' } })
        .success,
    ).toBe(false);
    expect(cloudConfigSchema.safeParse({ ...CLOUD, oidc: { ...CLOUD.oidc, clientId: '' } }).success).toBe(
      false,
    );
    expect(cloudConfigSchema.safeParse({ ...CLOUD, mode: 'local' }).success).toBe(false);
  });
});

describe('loadCloudConfig', () => {
  const reply = (body: BodyInit, init: ResponseInit) =>
    (async () => new Response(body, init)) as unknown as typeof fetch;

  it('returns the configuration a deployment publishes', async () => {
    const fetchImpl = vi.fn(
      reply(JSON.stringify(CLOUD), { headers: { 'content-type': 'application/json' } }),
    );
    await expect(loadCloudConfig(fetchImpl)).resolves.toEqual(CLOUD);
    expect(fetchImpl).toHaveBeenCalledWith('/config.json', expect.objectContaining({ cache: 'no-store' }));
  });

  it('returns nothing where there is no file (the local engine) or the answer is not JSON', async () => {
    await expect(loadCloudConfig(reply('', { status: 404 }))).resolves.toBeUndefined();
    await expect(
      loadCloudConfig(reply('<html>', { headers: { 'content-type': 'text/html' } })),
    ).resolves.toBeUndefined();
    await expect(
      loadCloudConfig((async () => Promise.reject(new TypeError('offline'))) as unknown as typeof fetch),
    ).resolves.toBeUndefined();
  });

  it('fails loudly on a file that exists but is wrong', async () => {
    const wrong = reply(JSON.stringify({ mode: 'cloud' }), {
      headers: { 'content-type': 'application/json' },
    });
    await expect(loadCloudConfig(wrong)).rejects.toBeInstanceOf(ConfigError);
  });
});

describe('resolveConnection', () => {
  it('a deployment config makes it the cloud, with the API on the page origin by default', () => {
    expect(resolveConnection(CLOUD, browser())).toEqual({
      mode: 'azure',
      profile: {
        apiBaseUrl: 'https://nodo.example',
        issuer: CLOUD.oidc.issuer,
        clientId: 'c',
        scope: 'api://nodo/access',
      },
    });
  });

  it('a separate API address is used as given, without a trailing slash', () => {
    const result = resolveConnection({ ...CLOUD, apiBaseUrl: 'https://api.example/' }, browser());
    expect(result).toMatchObject({ profile: { apiBaseUrl: 'https://api.example' } });
  });

  it('an address fragment cannot turn a deployment into a local page', () => {
    const page = browser({ hash: '#token=attacker' });
    expect(resolveConnection(CLOUD, page).mode).toBe('azure');
    expect(page.session && safeGet(page.session, LOCAL_TOKEN_KEY)).toBeNull();
  });

  it('local: takes the key from the fragment, keeps it for the tab and removes it from the address', () => {
    const page = browser({ hash: '#token=secret-key', pathname: '/meetings/', search: '?q=1' });
    expect(resolveConnection(undefined, page)).toEqual({
      mode: 'local',
      apiBaseUrl: 'https://nodo.example',
      token: 'secret-key',
    });
    expect(page.replaced).toEqual(['/meetings/?q=1']);
    expect(safeGet(page.session, LOCAL_TOKEN_KEY)).toBe('secret-key');
  });

  it('local: later loads of the tab use the stored key', () => {
    const session = storage();
    safeSet(session, LOCAL_TOKEN_KEY, 'stored');
    expect(resolveConnection(undefined, browser({ session }))).toMatchObject({
      mode: 'local',
      token: 'stored',
    });
  });

  it('local: still works when the address cannot be rewritten', () => {
    const page = browser({
      hash: '#token=k',
      replaceUrl: () => {
        throw new Error('no history');
      },
    });
    expect(resolveConnection(undefined, page)).toMatchObject({ mode: 'local', token: 'k' });
  });

  it('with nothing to go on it says so', () => {
    expect(resolveConnection(undefined, browser())).toEqual({ mode: 'none', reason: 'local' });
    expect(resolveConnection(undefined, browser({ hash: '#other=1' }))).toEqual({
      mode: 'none',
      reason: 'local',
    });
  });
});

describe('safe storage', () => {
  it('reads, writes and removes, and survives storage that throws', () => {
    const backing = storage();
    expect(safeSet(backing, 'k', 'v')).toBe(true);
    expect(safeGet(backing, 'k')).toBe('v');
    safeRemove(backing, 'k');
    expect(safeGet(backing, 'k')).toBeNull();

    const broken: StorageLike = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {
        throw new Error('blocked');
      },
    };
    expect(safeGet(broken, 'k')).toBeNull();
    expect(safeSet(broken, 'k', 'v')).toBe(false);
    expect(() => safeRemove(broken, 'k')).not.toThrow();
    expect(safeSet(undefined, 'k', 'v')).toBe(false);
  });
});
