// @vitest-environment node
import { createHash } from 'node:crypto';
import { beforeEach, describe, expect, it } from 'vitest';

import type { StorageLike } from '../runtime/storage';
import {
  AuthError,
  buildAuthorizationUrl,
  clearDiscoveryCache,
  completeAuthorization,
  refreshTokens,
} from './oidc';

const ISSUER = 'https://tenant.ciamlogin.com/tenant-id/v2.0';
const ORIGIN = 'https://nodo.example';
const PROFILE = { issuer: ISSUER, clientId: 'client-1', scope: 'api://nodo/access' };

const memory = (): StorageLike & { map: Map<string, string> } => {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
  };
};

const b64 = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');

/** A provider in a function: discovery, and a token endpoint that checks PKCE and the redirect URI. */
function provider() {
  const codes = new Map<string, { challenge: string; nonce: string; redirect: string }>();
  const refreshTokens = new Set(['good-refresh']);
  const seen: URLSearchParams[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    if (url.pathname.endsWith('/.well-known/openid-configuration')) {
      return Response.json({
        issuer: ISSUER,
        authorization_endpoint: 'https://tenant.ciamlogin.com/tenant-id/oauth2/v2.0/authorize',
        token_endpoint: 'https://tenant.ciamlogin.com/tenant-id/oauth2/v2.0/token',
        jwks_uri: 'https://tenant.ciamlogin.com/tenant-id/discovery/v2.0/keys',
      });
    }
    const form = new URLSearchParams(String(init?.body));
    seen.push(form);
    if (form.get('grant_type') === 'refresh_token') {
      if (!refreshTokens.has(form.get('refresh_token') ?? ''))
        return Response.json({ error: 'invalid_grant' }, { status: 400 });
      return Response.json({
        access_token: 'renewed',
        token_type: 'Bearer',
        expires_in: 600,
        refresh_token: 'next-refresh',
      });
    }
    const record = codes.get(form.get('code') ?? '');
    const verifier = form.get('code_verifier') ?? '';
    const pkce = createHash('sha256').update(verifier).digest('base64url');
    if (!record || record.challenge !== pkce || record.redirect !== form.get('redirect_uri')) {
      return Response.json({ error: 'invalid_grant' }, { status: 400 });
    }
    const now = Math.floor(Date.now() / 1000);
    const claims = {
      iss: ISSUER,
      aud: PROFILE.clientId,
      sub: 'u1',
      iat: now,
      exp: now + 300,
      nonce: record.nonce,
    };
    return Response.json({
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'Bearer',
      expires_in: 3600,
      id_token: `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64(claims)}.c2ln`,
    });
  }) as unknown as typeof fetch;
  return {
    fetchImpl,
    seen,
    /** What the user's browser does at the authorization endpoint: approve and come back. */
    approve(authorizeUrl: URL, state = authorizeUrl.searchParams.get('state')) {
      codes.set('code-1', {
        challenge: authorizeUrl.searchParams.get('code_challenge') ?? '',
        nonce: authorizeUrl.searchParams.get('nonce') ?? '',
        redirect: authorizeUrl.searchParams.get('redirect_uri') ?? '',
      });
      return new URL(`${ORIGIN}/auth/callback/?code=code-1&state=${state}`);
    },
  };
}

beforeEach(() => clearDiscoveryCache());

describe('buildAuthorizationUrl', () => {
  it('asks for the code flow with S256 PKCE, a state, a nonce and the API scope', async () => {
    const idp = provider();
    const session = memory();
    const { url, state } = await buildAuthorizationUrl(PROFILE, 'signin', '/meetings/', {
      session,
      origin: ORIGIN,
      fetchImpl: idp.fetchImpl,
    });
    const query = url.searchParams;
    expect(url.origin + url.pathname).toBe('https://tenant.ciamlogin.com/tenant-id/oauth2/v2.0/authorize');
    expect(query.get('response_type')).toBe('code');
    expect(query.get('client_id')).toBe('client-1');
    expect(query.get('redirect_uri')).toBe(`${ORIGIN}/auth/callback/`);
    expect(query.get('code_challenge_method')).toBe('S256');
    expect(query.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(query.get('state')).toBe(state);
    expect(query.get('nonce')).toBeTruthy();
    expect(query.get('scope')?.split(' ')).toEqual([
      'api://nodo/access',
      'openid',
      'profile',
      'offline_access',
    ]);
    expect(session.map.has(`nodo.oidc.tx.${state}`)).toBe(true);
  });

  it('a one-off sync token is asked for without a refresh token', async () => {
    const { url } = await buildAuthorizationUrl(PROFILE, 'sync', '/settings/', {
      session: memory(),
      origin: ORIGIN,
      fetchImpl: provider().fetchImpl,
    });
    expect(url.searchParams.get('scope')).not.toContain('offline_access');
  });

  it('uses a different state and challenge every time', async () => {
    const idp = provider();
    const deps = { session: memory(), origin: ORIGIN, fetchImpl: idp.fetchImpl };
    const first = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    const second = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    expect(first.state).not.toBe(second.state);
    expect(first.url.searchParams.get('code_challenge')).not.toBe(
      second.url.searchParams.get('code_challenge'),
    );
  });

  it('stops when the browser will not keep the sign-in record', async () => {
    await expect(
      buildAuthorizationUrl(PROFILE, 'signin', '/', {
        session: undefined,
        origin: ORIGIN,
        fetchImpl: provider().fetchImpl,
      }),
    ).rejects.toMatchObject({ code: 'state' });
  });

  it('reports an unreachable provider', async () => {
    const down = (async () => new Response('', { status: 500 })) as unknown as typeof fetch;
    await expect(
      buildAuthorizationUrl(PROFILE, 'signin', '/', { session: memory(), origin: ORIGIN, fetchImpl: down }),
    ).rejects.toMatchObject({
      code: 'discovery',
    });
  });
});

describe('completeAuthorization', () => {
  it('exchanges the code with the original verifier and returns tokens, where to go and why', async () => {
    const idp = provider();
    const session = memory();
    const deps = { session, origin: ORIGIN, fetchImpl: idp.fetchImpl, now: () => 5_000 };
    const { url } = await buildAuthorizationUrl(PROFILE, 'signin', '/ask/', deps);
    const result = await completeAuthorization(idp.approve(url), deps);
    expect(result).toEqual({
      tokens: { accessToken: 'access-1', refreshToken: 'refresh-1', expiresAt: 5_000 + 3600 * 1000 },
      returnTo: '/ask/',
      purpose: 'signin',
    });
    expect(session.map.size).toBe(0); // the transaction is spent
  });

  it('finds the transaction in shared storage when the pop-up has its own', async () => {
    const idp = provider();
    const shared = memory();
    const { url } = await buildAuthorizationUrl(PROFILE, 'sync', '/settings/', {
      session: shared,
      origin: ORIGIN,
      fetchImpl: idp.fetchImpl,
    });
    const result = await completeAuthorization(idp.approve(url), {
      session: memory(),
      shared,
      origin: ORIGIN,
      fetchImpl: idp.fetchImpl,
    });
    expect(result.purpose).toBe('sync');
  });

  it('refuses a callback whose state this browser never issued', async () => {
    const idp = provider();
    const deps = { session: memory(), origin: ORIGIN, fetchImpl: idp.fetchImpl };
    const { url } = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    await expect(completeAuthorization(idp.approve(url, 'forged-state'), deps)).rejects.toMatchObject({
      code: 'state',
    });
  });

  it('refuses a replayed callback', async () => {
    const idp = provider();
    const deps = { session: memory(), origin: ORIGIN, fetchImpl: idp.fetchImpl };
    const { url } = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    const callback = idp.approve(url);
    await completeAuthorization(callback, deps);
    await expect(completeAuthorization(callback, deps)).rejects.toMatchObject({ code: 'state' });
  });

  it('reports an error the provider put in the callback address', async () => {
    const idp = provider();
    const deps = { session: memory(), origin: ORIGIN, fetchImpl: idp.fetchImpl };
    const { url, state } = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    const denied = new URL(`${ORIGIN}/auth/callback/?error=access_denied&state=${state}`);
    expect(url.searchParams.get('state')).toBe(state);
    await expect(completeAuthorization(denied, deps)).rejects.toBeInstanceOf(AuthError);
  });

  it('fails when the provider will not accept the verifier', async () => {
    const idp = provider();
    const deps = { session: memory(), origin: ORIGIN, fetchImpl: idp.fetchImpl };
    const { url, state } = await buildAuthorizationUrl(PROFILE, 'signin', '/', deps);
    const other = new URL(url);
    other.searchParams.set('code_challenge', 'x'.repeat(43)); // a different challenge than the verifier's
    await expect(completeAuthorization(idp.approve(other, state), deps)).rejects.toMatchObject({
      code: 'exchange',
    });
  });
});

describe('refreshTokens', () => {
  it('renews and keeps the old refresh token when the provider sends none', async () => {
    const idp = provider();
    const tokens = await refreshTokens(PROFILE, 'good-refresh', {
      fetchImpl: idp.fetchImpl,
      now: () => 1000,
    });
    expect(tokens).toEqual({
      accessToken: 'renewed',
      refreshToken: 'next-refresh',
      expiresAt: 1000 + 600 * 1000,
    });
    expect(idp.seen.at(-1)?.get('grant_type')).toBe('refresh_token');
    expect(idp.seen.at(-1)?.get('client_id')).toBe('client-1');
  });

  it('reports a refresh token the provider no longer knows', async () => {
    await expect(
      refreshTokens(PROFILE, 'revoked', { fetchImpl: provider().fetchImpl }),
    ).rejects.toMatchObject({ code: 'refresh' });
  });
});
