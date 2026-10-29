import type { CloudConfig } from './config';
import { safeGet, safeRemove, safeSet, type StorageLike } from './storage';

export const LOCAL_TOKEN_KEY = 'nodo.localToken';

export interface AzureProfile {
  apiBaseUrl: string;
  issuer: string;
  clientId: string;
  scope: string;
}

export type Connection =
  | { mode: 'local'; apiBaseUrl: string; token: string }
  | { mode: 'azure'; profile: AzureProfile }
  | { mode: 'none'; reason: 'local' | 'cloud' };

export interface BrowserContext {
  origin: string;
  pathname: string;
  search: string;
  hash: string;
  session?: StorageLike;
  replaceUrl(url: string): void;
}

function fragmentToken(hash: string): string | undefined {
  return new URLSearchParams(hash.replace(/^#/, '')).get('token')?.trim() || undefined;
}

/**
 * Which backend this page talks to. A deployment that publishes a config is the cloud and nothing
 * else: an address fragment must not turn it into a "local" page. Without a config the page belongs
 * to the local engine, which hands its access key over in the address fragment (never sent to a
 * server); the key is kept for the tab and the fragment is removed from the address.
 */
export function resolveConnection(config: CloudConfig | undefined, browser: BrowserContext): Connection {
  if (config) {
    const { issuer, clientId, scope } = config.oidc;
    return {
      mode: 'azure',
      profile: {
        apiBaseUrl: (config.apiBaseUrl || browser.origin).replace(/\/+$/, ''),
        issuer,
        clientId,
        scope,
      },
    };
  }
  const fromFragment = fragmentToken(browser.hash);
  if (fromFragment) {
    safeSet(browser.session, LOCAL_TOKEN_KEY, fromFragment);
    try {
      browser.replaceUrl(`${browser.pathname}${browser.search}`);
    } catch {
      // Removing the fragment is hygiene; history can be unavailable in embedded views.
    }
    return { mode: 'local', apiBaseUrl: browser.origin, token: fromFragment };
  }
  const stored = safeGet(browser.session, LOCAL_TOKEN_KEY);
  if (stored) return { mode: 'local', apiBaseUrl: browser.origin, token: stored };
  return { mode: 'none', reason: 'local' };
}

export function forgetLocalToken(session: StorageLike | undefined): void {
  safeRemove(session, LOCAL_TOKEN_KEY);
}
