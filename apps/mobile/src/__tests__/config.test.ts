import { ConfigError, readConfig } from '../config';

describe('readConfig', () => {
  const runtime = globalThis as typeof globalThis & { __DEV__: boolean };
  const originalDev = runtime.__DEV__;

  afterEach(() => {
    runtime.__DEV__ = originalDev;
  });

  it('defaults to mock mode without cloud configuration', () => {
    expect(readConfig({})).toEqual({
      dataSource: 'mock',
      apiBaseUrl: '',
      oidc: { issuer: '', clientId: '', apiScope: '' },
    });
  });

  it('accepts complete cloud configuration', () => {
    expect(
      readConfig({
        EXPO_PUBLIC_DATA_SOURCE: 'cloud',
        EXPO_PUBLIC_API_BASE_URL: 'https://api.example.com/',
        EXPO_PUBLIC_OIDC_ISSUER: 'https://login.example.com/tenant/v2.0',
        EXPO_PUBLIC_OIDC_CLIENT_ID: 'client-id',
        EXPO_PUBLIC_OIDC_API_SCOPE: 'api://client/access',
      }),
    ).toMatchObject({ dataSource: 'cloud', apiBaseUrl: 'https://api.example.com' });
  });

  it('allows local http and a development token in development', () => {
    runtime.__DEV__ = true;
    const config = readConfig({
      EXPO_PUBLIC_DATA_SOURCE: 'cloud',
      EXPO_PUBLIC_API_BASE_URL: 'http://10.0.2.2:8000',
      EXPO_PUBLIC_DEV_TOKEN: 'local-token',
    });
    expect(config.devToken).toBe('local-token');
  });

  it('ignores a development token outside development', () => {
    runtime.__DEV__ = false;
    expect(() =>
      readConfig({
        EXPO_PUBLIC_DATA_SOURCE: 'cloud',
        EXPO_PUBLIC_API_BASE_URL: 'https://api.example.com',
        EXPO_PUBLIC_DEV_TOKEN: 'ignored',
      }),
    ).toThrow(ConfigError);
  });

  it('reports every invalid or missing cloud variable', () => {
    try {
      readConfig({
        EXPO_PUBLIC_DATA_SOURCE: 'cloud',
        EXPO_PUBLIC_API_BASE_URL: 'http://example.com',
        EXPO_PUBLIC_OIDC_ISSUER: 'not-a-url',
      });
      throw new Error('Expected readConfig to throw');
    } catch (error) {
      expect(error).toBeInstanceOf(ConfigError);
      expect((error as Error).message).toContain('EXPO_PUBLIC_API_BASE_URL');
      expect((error as Error).message).toContain('EXPO_PUBLIC_OIDC_ISSUER');
      expect((error as Error).message).toContain('EXPO_PUBLIC_OIDC_CLIENT_ID');
      expect((error as Error).message).toContain('EXPO_PUBLIC_OIDC_API_SCOPE');
    }
  });
});
