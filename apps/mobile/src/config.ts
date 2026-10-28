export type DataSource = 'mock' | 'cloud';

export interface AppConfig {
  dataSource: DataSource;
  apiBaseUrl: string;
  oidc: { issuer: string; clientId: string; apiScope: string };
  devToken?: string;
}

export class ConfigError extends Error {
  constructor(public readonly issues: string[]) {
    super(`Invalid configuration:\n${issues.map((issue) => `- ${issue}`).join('\n')}`);
    this.name = 'ConfigError';
  }
}

function validUrl(value: string, allowLocalHttp: boolean): boolean {
  try {
    const url = new URL(value);
    const local = ['localhost', '127.0.0.1', '10.0.2.2'].includes(url.hostname);
    return url.protocol === 'https:' || (allowLocalHttp && local && url.protocol === 'http:');
  } catch {
    return false;
  }
}

export function readConfig(env: Record<string, string | undefined>): AppConfig {
  const source = env.EXPO_PUBLIC_DATA_SOURCE ?? 'mock';
  const issues: string[] = [];
  if (source !== 'mock' && source !== 'cloud') issues.push('EXPO_PUBLIC_DATA_SOURCE must be mock or cloud');
  const dataSource: DataSource = source === 'cloud' ? 'cloud' : 'mock';
  const apiBaseUrl = (env.EXPO_PUBLIC_API_BASE_URL ?? '').replace(/\/+$/, '');
  const issuer = (env.EXPO_PUBLIC_OIDC_ISSUER ?? '').replace(/\/+$/, '');
  const clientId = env.EXPO_PUBLIC_OIDC_CLIENT_ID ?? '';
  const apiScope = env.EXPO_PUBLIC_OIDC_API_SCOPE ?? '';
  const devToken = typeof __DEV__ !== 'undefined' && __DEV__ ? env.EXPO_PUBLIC_DEV_TOKEN : undefined;

  if (dataSource === 'cloud') {
    if (!apiBaseUrl || !validUrl(apiBaseUrl, true)) {
      issues.push('EXPO_PUBLIC_API_BASE_URL must be an HTTPS URL, or HTTP on localhost, 127.0.0.1, or 10.0.2.2');
    }
    if (!devToken) {
      if (!issuer || !validUrl(issuer, true)) {
        issues.push('EXPO_PUBLIC_OIDC_ISSUER must be HTTPS, or HTTP on localhost, 127.0.0.1, or 10.0.2.2');
      }
      if (!clientId) issues.push('EXPO_PUBLIC_OIDC_CLIENT_ID is required');
      if (!apiScope) issues.push('EXPO_PUBLIC_OIDC_API_SCOPE is required');
    }
  }
  if (issues.length) throw new ConfigError(issues);
  return { dataSource, apiBaseUrl, oidc: { issuer, clientId, apiScope }, ...(devToken ? { devToken } : {}) };
}

export const config = readConfig(process.env);
