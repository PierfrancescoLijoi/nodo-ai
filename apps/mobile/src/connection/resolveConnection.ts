import { readConfig } from '@/config';

import type { AzureProfile, ResolvedConnection } from './types';

export const LOCAL_TOKEN_KEY = 'nodo.localToken';
export const AZURE_PROFILE_KEY = 'nodo.profile';
export const AZURE_TARGET_KEY = 'nodo.azureTarget';

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface BrowserEnvironment {
  origin: string;
  pathname: string;
  search: string;
  hash: string;
  sessionStorage?: StorageLike;
  localStorage?: StorageLike;
  replaceUrl(url: string): void;
}

function safeGet(storage: StorageLike | undefined, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function safeSet(storage: StorageLike | undefined, key: string, value: string): boolean {
  try {
    storage?.setItem(key, value);
    return !!storage;
  } catch {
    return false;
  }
}

export function safeRemove(storage: StorageLike | undefined, key: string): void {
  try {
    storage?.removeItem(key);
  } catch {
    // Storage can be unavailable in private or locked-down browser contexts.
  }
}

function isAllowedUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const local = ['localhost', '127.0.0.1', '10.0.2.2'].includes(url.hostname);
    return url.protocol === 'https:' || (url.protocol === 'http:' && local);
  } catch {
    return false;
  }
}

export function normalizeAzureProfile(value: unknown): AzureProfile | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const candidate = value as Record<string, unknown>;
  const apiBaseUrl = typeof candidate.apiBaseUrl === 'string' ? candidate.apiBaseUrl.replace(/\/+$/, '') : '';
  const issuer = typeof candidate.issuer === 'string' ? candidate.issuer.replace(/\/+$/, '') : '';
  const clientId = typeof candidate.clientId === 'string' ? candidate.clientId.trim() : '';
  const apiScope = typeof candidate.apiScope === 'string' ? candidate.apiScope.trim() : '';
  if (!isAllowedUrl(apiBaseUrl) || !isAllowedUrl(issuer) || !clientId || !apiScope) return undefined;
  return { apiBaseUrl, issuer, clientId, apiScope };
}

export function readStoredProfile(storage: StorageLike | undefined, key = AZURE_PROFILE_KEY): AzureProfile | undefined {
  const raw = safeGet(storage, key);
  if (!raw) return undefined;
  try {
    return normalizeAzureProfile(JSON.parse(raw));
  } catch {
    return undefined;
  }
}

function fragmentToken(hash: string): string | undefined {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  return params.get('token')?.trim() || undefined;
}

export function resolveConnection(
  env: Record<string, string | undefined>,
  browser?: BrowserEnvironment,
): ResolvedConnection {
  if (browser) {
    const fromFragment = fragmentToken(browser.hash);
    if (fromFragment) {
      safeSet(browser.sessionStorage, LOCAL_TOKEN_KEY, fromFragment);
      try {
        browser.replaceUrl(`${browser.pathname}${browser.search}`);
      } catch {
        // Removing the fragment is privacy hardening, but history can be unavailable in tests or embedded browsers.
      }
      return { mode: 'local', apiBaseUrl: browser.origin, token: fromFragment };
    }
    const fromSession = safeGet(browser.sessionStorage, LOCAL_TOKEN_KEY);
    if (fromSession) return { mode: 'local', apiBaseUrl: browser.origin, token: fromSession };
    const profile = readStoredProfile(browser.localStorage);
    if (profile) return { mode: 'azure', profile };
  }

  if (env.EXPO_PUBLIC_DATA_SOURCE === 'cloud') {
    try {
      const build = readConfig(env);
      return {
        mode: 'azure',
        source: 'build',
        profile: { apiBaseUrl: build.apiBaseUrl, ...build.oidc },
        ...(build.devToken ? { devToken: build.devToken } : {}),
      };
    } catch {
      return { mode: 'setup' };
    }
  }
  return { mode: 'setup' };
}

export function browserEnvironment(): BrowserEnvironment | undefined {
  if (typeof window === 'undefined') return undefined;
  return {
    origin: window.location.origin,
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
    sessionStorage: window.sessionStorage,
    localStorage: window.localStorage,
    replaceUrl: (url) => window.history.replaceState(null, '', url),
  };
}
