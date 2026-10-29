import { createContext, useContext, useEffect, useMemo, useState, type DependencyList, type ReactNode } from 'react';

import { usePrefs } from '@/prefs/PrefsProvider';
import { useAuth } from '@/auth/AuthProvider';
import { useConnection } from '@/connection/ConnectionProvider';

import { createApiClient } from './http/client';
import { createHttpRepository } from './http/httpRepository';
import { createMockRepository } from './mock/mockRepository';
import type { MeetingsRepository } from './types';

const RepositoryContext = createContext<MeetingsRepository | null>(null);

// Swap createMockRepository for an HTTP adapter generated from packages/contracts once the API is deployed.
export function RepositoryProvider({ children }: { children: ReactNode }) {
  const { locale } = usePrefs();
  const auth = useAuth();
  const { connection } = useConnection();
  const repository = useMemo(() => {
    if (connection.mode === 'mock' || connection.mode === 'setup') return createMockRepository(locale);
    const baseUrl = connection.mode === 'local' ? connection.apiBaseUrl : connection.profile.apiBaseUrl;
    const client = createApiClient({
      baseUrl,
      getAccessToken: auth.getAccessToken,
      onUnauthorized: auth.signOut,
    });
    return createHttpRepository(client, locale, { mode: connection.mode });
  }, [auth.getAccessToken, auth.signOut, connection, locale]);
  return <RepositoryContext.Provider value={repository}>{children}</RepositoryContext.Provider>;
}

export function useRepository(): MeetingsRepository {
  const value = useContext(RepositoryContext);
  if (!value) throw new Error('useRepository must be used inside RepositoryProvider');
  return value;
}

type Settled<T> = { status: 'ready'; data: T } | { status: 'error'; error: Error };

export type AsyncState<T> =
  { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; error: Error; retry: () => void };

interface Stored<T> {
  repository: MeetingsRepository;
  key: string;
  settled: Settled<T>;
}

/**
 * Loads data from the repository. A result only counts for the exact repository (locale) and inputs
 * it was loaded for, so a change shows `loading` immediately instead of one frame of stale data.
 */
export function useAsync<T>(
  load: (repository: MeetingsRepository) => Promise<T>,
  deps: DependencyList = [],
): AsyncState<T> {
  const repository = useRepository();
  const [attempt, setAttempt] = useState(0);
  const [stored, setStored] = useState<Stored<T> | null>(null);
  const key = JSON.stringify([...deps, attempt]);

  useEffect(() => {
    let cancelled = false;
    const settle = (settled: Settled<T>) => {
      if (!cancelled) setStored({ repository, key, settled });
    };
    load(repository).then(
      (data) => settle({ status: 'ready', data }),
      (error: unknown) => settle({ status: 'error', error: error instanceof Error ? error : new Error(String(error)) }),
    );
    return () => {
      cancelled = true;
    };
    // `load` is an inline closure whose inputs are covered by `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repository, key]);

  if (!stored || stored.repository !== repository || stored.key !== key) return { status: 'loading' };
  if (stored.settled.status === 'error') {
    return { status: 'error', error: stored.settled.error, retry: () => setAttempt((n) => n + 1) };
  }
  return stored.settled;
}
