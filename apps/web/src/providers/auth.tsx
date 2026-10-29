'use client';

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { buildAuthorizationUrl, refreshTokens, type OidcProfile, type Tokens } from '@/lib/auth/oidc';
import { createSession, sessionTokenStore } from '@/lib/auth/session';
import { forgetLocalToken, type Connection } from '@/lib/runtime/connection';
import { sessionStore } from '@/lib/runtime/storage';

export type AuthStatus = 'loading' | 'signedOut' | 'signedIn';

export interface AuthValue {
  status: AuthStatus;
  error?: Error;
  /** Starts the sign-in; the browser leaves for the identity provider and comes back to the callback page. */
  signIn(returnTo?: string): Promise<void>;
  signOut(): void;
  getAccessToken(forceRefresh?: boolean): Promise<string>;
  /** Called by the callback page once the code was exchanged. */
  acceptTokens(tokens: Tokens): void;
}

const AuthContext = createContext<AuthValue | null>(null);

/** The local engine's key stands in for a token; there is nothing to sign in to. */
function LocalAuth({ token, children }: { token: string; children: ReactNode }) {
  const value = useMemo<AuthValue>(
    () => ({
      status: 'signedIn',
      signIn: async () => undefined,
      signOut: () => forgetLocalToken(sessionStore()),
      getAccessToken: async () => token,
      acceptTokens: () => undefined,
    }),
    [token],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function OidcAuth({ profile, children }: { profile: OidcProfile; children: ReactNode }) {
  // Mounted only in the browser (the gate renders a placeholder until preferences and connection are known).
  const store = useMemo(() => sessionTokenStore(sessionStore()), []);
  const [status, setStatus] = useState<AuthStatus>(() => (store.get() ? 'signedIn' : 'signedOut'));
  const [error, setError] = useState<Error>();

  const session = useMemo(
    () =>
      createSession({
        store,
        now: Date.now,
        refresh: (refreshToken) => refreshTokens(profile, refreshToken, {}),
        onSignedOut: () => setStatus('signedOut'),
      }),
    [profile, store],
  );

  const signIn = useCallback(
    async (returnTo = '/') => {
      setError(undefined);
      try {
        const { url } = await buildAuthorizationUrl(profile, 'signin', returnTo, {
          session: sessionStore(),
          origin: window.location.origin,
        });
        window.location.assign(url);
      } catch (cause) {
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      }
    },
    [profile],
  );

  const acceptTokens = useCallback(
    (tokens: Tokens) => {
      store.set(tokens);
      setStatus('signedIn');
    },
    [store],
  );

  const value = useMemo<AuthValue>(
    () => ({
      status,
      error,
      signIn,
      signOut: session.signOut,
      getAccessToken: session.getAccessToken,
      acceptTokens,
    }),
    [acceptTokens, error, session, signIn, status],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ connection, children }: { connection: Connection; children: ReactNode }) {
  if (connection.mode === 'local') return <LocalAuth token={connection.token}>{children}</LocalAuth>;
  if (connection.mode === 'azure') {
    const { issuer, clientId, scope } = connection.profile;
    return (
      <OidcAuth profile={{ issuer, clientId, scope }} key={`${issuer}|${clientId}|${scope}`}>
        {children}
      </OidcAuth>
    );
  }
  return <NoAuth>{children}</NoAuth>;
}

function NoAuth({ children }: { children: ReactNode }) {
  const value = useMemo<AuthValue>(
    () => ({
      status: 'signedOut',
      signIn: async () => undefined,
      signOut: () => undefined,
      getAccessToken: async () => {
        throw new Error('Not connected');
      },
      acceptTokens: () => undefined,
    }),
    [],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
