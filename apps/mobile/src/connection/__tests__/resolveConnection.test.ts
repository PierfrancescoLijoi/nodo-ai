import { resolveConnection, type BrowserEnvironment } from '../resolveConnection';

function storage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: jest.fn((key: string) => values.get(key) ?? null),
    setItem: jest.fn((key: string, value: string) => values.set(key, value)),
    removeItem: jest.fn((key: string) => values.delete(key)),
  };
}

function browser(overrides: Partial<BrowserEnvironment> = {}): BrowserEnvironment {
  return {
    origin: 'http://127.0.0.1:4312',
    pathname: '/',
    search: '',
    hash: '',
    sessionStorage: storage(),
    localStorage: storage(),
    replaceUrl: jest.fn(),
    ...overrides,
  };
}

describe('resolveConnection', () => {
  it('selects local, stores a fragment token, and removes the fragment', () => {
    const environment = browser({ hash: '#token=secret%20value' });
    expect(resolveConnection({}, environment)).toEqual({
      mode: 'local',
      apiBaseUrl: 'http://127.0.0.1:4312',
      token: 'secret value',
    });
    expect(environment.sessionStorage?.setItem).toHaveBeenCalledWith('nodo.localToken', 'secret value');
    expect(environment.replaceUrl).toHaveBeenCalledWith('/');
  });

  it('uses the session token when storage reads succeed', () => {
    const environment = browser({ sessionStorage: storage({ 'nodo.localToken': 'remembered' }) });
    expect(resolveConnection({}, environment)).toMatchObject({ mode: 'local', token: 'remembered' });
  });

  it('ignores storage failures', () => {
    const broken = storage();
    broken.getItem.mockImplementation(() => {
      throw new Error('disabled');
    });
    expect(resolveConnection({}, browser({ sessionStorage: broken, localStorage: broken }))).toEqual({ mode: 'setup' });
  });

  it('selects a valid stored Azure profile', () => {
    const profile = {
      apiBaseUrl: 'https://api.example.com/',
      issuer: 'https://login.example.com/',
      clientId: 'client',
      apiScope: 'api://nodo/access',
    };
    const environment = browser({ localStorage: storage({ 'nodo.profile': JSON.stringify(profile) }) });
    expect(resolveConnection({}, environment)).toEqual({
      mode: 'azure',
      profile: { ...profile, apiBaseUrl: 'https://api.example.com', issuer: 'https://login.example.com' },
    });
  });

  it('rejects insecure non-local profiles and falls back to setup', () => {
    const profile = {
      apiBaseUrl: 'http://api.example.com',
      issuer: 'https://login.example.com',
      clientId: 'client',
      apiScope: 'scope',
    };
    expect(
      resolveConnection({}, browser({ localStorage: storage({ 'nodo.profile': JSON.stringify(profile) }) })),
    ).toEqual({
      mode: 'setup',
    });
  });

  it('keeps the build-time cloud configuration working', () => {
    expect(
      resolveConnection(
        {
          EXPO_PUBLIC_DATA_SOURCE: 'cloud',
          EXPO_PUBLIC_API_BASE_URL: 'https://api.example.com',
          EXPO_PUBLIC_OIDC_ISSUER: 'https://login.example.com',
          EXPO_PUBLIC_OIDC_CLIENT_ID: 'client',
          EXPO_PUBLIC_OIDC_API_SCOPE: 'scope',
        },
        browser(),
      ),
    ).toMatchObject({ mode: 'azure', source: 'build' });
  });

  it('returns setup when no connection applies', () => {
    expect(resolveConnection({}, browser())).toEqual({ mode: 'setup' });
  });
});
