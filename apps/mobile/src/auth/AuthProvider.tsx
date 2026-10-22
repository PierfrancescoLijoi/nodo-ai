import * as AuthSession from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { config } from '@/config';

import { createSession, type StoredTokens } from './session';
import { tokenStore } from './tokenStore';

WebBrowser.maybeCompleteAuthSession();

export type AuthStatus = 'loading' | 'signedOut' | 'signedIn';

interface AuthValue {
  status: AuthStatus;
  error?: Error;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  getAccessToken(forceRefresh?: boolean): Promise<string>;
}

const AuthContext = createContext<AuthValue | null>(null);

const toStoredTokens = (token: AuthSession.TokenResponse, fallbackRefreshToken?: string): StoredTokens => ({
  accessToken: token.accessToken,
  refreshToken: token.refreshToken ?? fallbackRefreshToken,
  expiresAt: (token.issuedAt + (token.expiresIn ?? 3600)) * 1000,
});

function useSession(discovery: AuthSession.DiscoveryDocument | null, setStatus: (status: AuthStatus) => void) {
  const { clientId, apiScope } = config.oidc;
  return useMemo(
    () =>
      createSession({
        store: tokenStore,
        now: Date.now,
        refresh: async (refreshToken) => {
          if (!discovery) throw new Error('Identity provider discovery is not ready');
          const token = await AuthSession.refreshAsync({ clientId, refreshToken, scopes: [apiScope] }, discovery);
          return toStoredTokens(token, refreshToken);
        },
        onSignedOut: () => setStatus('signedOut'),
      }),
    [apiScope, clientId, discovery, setStatus],
  );
}

function useCodeExchange(
  request: AuthSession.AuthRequest | null,
  response: AuthSession.AuthSessionResult | null,
  discovery: AuthSession.DiscoveryDocument | null,
  redirectUri: string,
  onSuccess: () => void,
  onError: (error: Error) => void,
) {
  const { clientId } = config.oidc;
  useEffect(() => {
    if (response?.type !== 'success' || !response.params.code || !request?.codeVerifier || !discovery) return;
    void AuthSession.exchangeCodeAsync(
      { clientId, code: response.params.code, redirectUri, extraParams: { code_verifier: request.codeVerifier } },
      discovery,
    ).then(
      async (token) => {
        await tokenStore.set(toStoredTokens(token));
        onSuccess();
      },
      (cause: unknown) => onError(cause instanceof Error ? cause : new Error(String(cause))),
    );
  }, [clientId, discovery, onError, onSuccess, redirectUri, request, response]);
}

function DevAuthProvider({ children, token }: { children: ReactNode; token: string }) {
  const value = useMemo<AuthValue>(
    () => ({
      status: 'signedIn',
      signIn: async () => undefined,
      signOut: async () => undefined,
      getAccessToken: async () => token,
    }),
    [token],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function OidcAuthProvider({ children }: { children: ReactNode }) {
  const { issuer, clientId, apiScope } = config.oidc;
  const discovery = AuthSession.useAutoDiscovery(issuer);
  const redirectUri = AuthSession.makeRedirectUri({ scheme: 'nodo', path: 'auth' });
  const [request, response, promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      redirectUri,
      responseType: AuthSession.ResponseType.Code,
      scopes: [apiScope, 'openid', 'profile', 'offline_access'],
      usePKCE: true,
    },
    discovery,
  );
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [error, setError] = useState<Error>();
  const session = useSession(discovery, setStatus);

  useEffect(() => {
    void tokenStore.get().then(
      (tokens) => setStatus(tokens ? 'signedIn' : 'signedOut'),
      () => setStatus('signedOut'),
    );
  }, []);

  const exchangeSucceeded = useCallback(() => setStatus('signedIn'), []);
  const exchangeFailed = useCallback((cause: Error) => {
    setError(cause);
    setStatus('signedOut');
  }, []);
  useCodeExchange(request, response, discovery, redirectUri, exchangeSucceeded, exchangeFailed);

  const signIn = useCallback(async () => {
    setError(undefined);
    try {
      await promptAsync();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    }
  }, [promptAsync]);
  const value = useMemo<AuthValue>(
    () => ({ status, error, signIn, signOut: session.signOut, getAccessToken: session.getAccessToken }),
    [error, session.getAccessToken, session.signOut, signIn, status],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  if (config.dataSource === 'mock') {
    return <DevAuthProvider token="mock">{children}</DevAuthProvider>;
  }
  return config.devToken ? (
    <DevAuthProvider token={config.devToken}>{children}</DevAuthProvider>
  ) : (
    <OidcAuthProvider>{children}</OidcAuthProvider>
  );
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
