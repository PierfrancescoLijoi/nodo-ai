// Authorization code flow with PKCE for a public (browser) client, on top of oauth4webapi: discovery,
// state and nonce checks, token requests and ID token claim validation come from the library; this file
// only decides what to send and where to keep the one-use transaction.
import * as oauth from 'oauth4webapi';

import { safeGet, safeRemove, safeSet, type StorageLike } from '../runtime/storage';

export interface OidcProfile {
  issuer: string;
  clientId: string;
  scope: string;
}

export interface Tokens {
  accessToken: string;
  refreshToken?: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

/** `signin` starts the session of this tab; `sync` fetches one short-lived token for the Copy to Azure action. */
export type Purpose = 'signin' | 'sync';

interface Transaction {
  verifier: string;
  nonce: string;
  redirectUri: string;
  returnTo: string;
  purpose: Purpose;
  profile: OidcProfile;
}

export interface OidcDeps {
  /** Where the one-use transaction is kept: the tab's own storage, or storage shared by the origin when the
   *  sign-in runs in a pop-up (the identity provider may cut the pop-up off from its opener). */
  session: StorageLike | undefined;
  /** Where the callback also looks for a transaction. */
  shared?: StorageLike | undefined;
  origin: string;
  fetchImpl?: typeof fetch;
  now?: () => number;
}

export class AuthError extends Error {
  constructor(
    message: string,
    public readonly code: 'callback' | 'state' | 'exchange' | 'refresh' | 'discovery',
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

const TX_PREFIX = 'nodo.oidc.tx.';
const DEFAULT_LIFETIME_SECONDS = 3600;
const client = (profile: OidcProfile): oauth.Client => ({ client_id: profile.clientId });

export const callbackUri = (origin: string) => `${origin}/auth/callback/`;

/** HTTP is accepted by the library only when asked for, and only for a developer's own machine. */
function requestOptions(profile: OidcProfile, deps: Pick<OidcDeps, 'fetchImpl'>) {
  const insecure = new URL(profile.issuer).protocol === 'http:';
  return {
    ...(insecure ? { [oauth.allowInsecureRequests]: true } : {}),
    ...(deps.fetchImpl ? { [oauth.customFetch]: deps.fetchImpl } : {}),
  };
}

const metadata = new Map<string, Promise<oauth.AuthorizationServer>>();

async function discover(
  profile: OidcProfile,
  deps: Pick<OidcDeps, 'fetchImpl'>,
): Promise<oauth.AuthorizationServer> {
  const cached = metadata.get(profile.issuer);
  if (cached) return cached;
  const issuer = new URL(profile.issuer);
  const pending = oauth
    .discoveryRequest(issuer, { algorithm: 'oidc', ...requestOptions(profile, deps) })
    .then((response) => oauth.processDiscoveryResponse(issuer, response))
    .catch((cause: unknown) => {
      metadata.delete(profile.issuer);
      throw new AuthError(`Identity provider discovery failed: ${String(cause)}`, 'discovery');
    });
  metadata.set(profile.issuer, pending);
  return pending;
}

export function clearDiscoveryCache(): void {
  metadata.clear();
}

export async function buildAuthorizationUrl(
  profile: OidcProfile,
  purpose: Purpose,
  returnTo: string,
  deps: OidcDeps,
): Promise<{ url: URL; state: string }> {
  const server = await discover(profile, deps);
  if (!server.authorization_endpoint)
    throw new AuthError('The provider has no authorization endpoint', 'discovery');
  const verifier = oauth.generateRandomCodeVerifier();
  const state = oauth.generateRandomState();
  const nonce = oauth.generateRandomNonce();
  const redirectUri = callbackUri(deps.origin);
  const transaction: Transaction = { verifier, nonce, redirectUri, returnTo, purpose, profile };
  if (!safeSet(deps.session, `${TX_PREFIX}${state}`, JSON.stringify(transaction))) {
    throw new AuthError(
      'This browser does not allow the sign-in to be started (storage is blocked)',
      'state',
    );
  }

  const url = new URL(server.authorization_endpoint);
  const scopes = [profile.scope, 'openid', 'profile', ...(purpose === 'signin' ? ['offline_access'] : [])];
  url.search = new URLSearchParams({
    client_id: profile.clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: scopes.join(' '),
    code_challenge: await oauth.calculatePKCECodeChallenge(verifier),
    code_challenge_method: 'S256',
    state,
    nonce,
  }).toString();
  return { url, state };
}

function takeTransaction(callback: URL, stores: (StorageLike | undefined)[]): Transaction {
  const state = callback.searchParams.get('state');
  const holder = state ? stores.find((store) => safeGet(store, `${TX_PREFIX}${state}`) !== null) : undefined;
  const raw = state ? safeGet(holder, `${TX_PREFIX}${state}`) : null;
  if (!state || !raw) throw new AuthError('The sign-in was not started in this browser', 'state');
  for (const store of stores) safeRemove(store, `${TX_PREFIX}${state}`); // one use: a replayed callback finds nothing
  try {
    return JSON.parse(raw) as Transaction;
  } catch {
    throw new AuthError('The sign-in record is unreadable', 'state');
  }
}

function toTokens(response: oauth.TokenEndpointResponse, now: number, previousRefresh?: string): Tokens {
  const refreshToken = response.refresh_token ?? previousRefresh;
  return {
    accessToken: response.access_token,
    ...(refreshToken ? { refreshToken } : {}),
    expiresAt: now + (response.expires_in ?? DEFAULT_LIFETIME_SECONDS) * 1000,
  };
}

/** Finishes a sign-in from the address the provider returned to. */
export async function completeAuthorization(
  callback: URL,
  deps: OidcDeps,
): Promise<{ tokens: Tokens; returnTo: string; purpose: Purpose }> {
  const transaction = takeTransaction(callback, [deps.session, deps.shared]);
  const { profile } = transaction;
  const server = await discover(profile, deps);
  const now = (deps.now ?? Date.now)();
  try {
    const parameters = oauth.validateAuthResponse(
      server,
      client(profile),
      callback.searchParams,
      callback.searchParams.get('state') ?? oauth.expectNoState,
    );
    const response = await oauth.authorizationCodeGrantRequest(
      server,
      client(profile),
      oauth.None(),
      parameters,
      transaction.redirectUri,
      transaction.verifier,
      requestOptions(profile, deps),
    );
    const result = await oauth.processAuthorizationCodeResponse(server, client(profile), response, {
      expectedNonce: transaction.nonce,
      requireIdToken: true,
    });
    return { tokens: toTokens(result, now), returnTo: transaction.returnTo, purpose: transaction.purpose };
  } catch (cause) {
    throw new AuthError(`Sign-in could not be completed: ${String(cause)}`, 'exchange');
  }
}

export async function refreshTokens(
  profile: OidcProfile,
  refreshToken: string,
  deps: Pick<OidcDeps, 'fetchImpl' | 'now'>,
): Promise<Tokens> {
  const server = await discover(profile, deps);
  const now = (deps.now ?? Date.now)();
  try {
    const response = await oauth.refreshTokenGrantRequest(
      server,
      client(profile),
      oauth.None(),
      refreshToken,
      requestOptions(profile, deps),
    );
    return toTokens(
      await oauth.processRefreshTokenResponse(server, client(profile), response),
      now,
      refreshToken,
    );
  } catch (cause) {
    throw new AuthError(`The session could not be renewed: ${String(cause)}`, 'refresh');
  }
}
