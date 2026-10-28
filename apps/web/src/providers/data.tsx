'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

import { ApiError, createApiClient } from '@/lib/api/client';
import { createLocalEngineApi, type LocalEngineApi } from '@/lib/api/local';
import { createRepository, type Repository } from '@/lib/api/repository';
import type { Connection } from '@/lib/runtime/connection';
import { useAuth } from './auth';

interface Data {
  repository: Repository;
  /** Present on the local engine only. */
  engine?: LocalEngineApi;
}

const DataContext = createContext<Data | null>(null);

const STALE_MS = 15_000;
const MAX_RETRIES = 2;

/** Server trouble and dropped connections are worth another try; a refusal (4xx) will not change. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  if (error instanceof ApiError) return error.status === 0 ? error.code !== 'cancelled' : error.status >= 500;
  return false;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: STALE_MS, retry: shouldRetry, refetchOnWindowFocus: false },
    },
  });
}

export function DataProvider({ connection, children }: { connection: Connection; children: ReactNode }) {
  const auth = useAuth();
  const [queryClient] = useState(createQueryClient);
  const data = useMemo<Data | null>(() => {
    if (connection.mode === 'none') return null;
    const baseUrl = connection.mode === 'local' ? connection.apiBaseUrl : connection.profile.apiBaseUrl;
    const client = createApiClient({
      baseUrl,
      getAccessToken: auth.getAccessToken,
      onUnauthorized: auth.signOut,
    });
    return {
      repository: createRepository(client, { mode: connection.mode === 'local' ? 'local' : 'azure' }),
      ...(connection.mode === 'local' ? { engine: createLocalEngineApi(client) } : {}),
    };
  }, [auth.getAccessToken, auth.signOut, connection]);

  if (!data) return <>{children}</>;
  return (
    <QueryClientProvider client={queryClient}>
      <DataContext.Provider value={data}>{children}</DataContext.Provider>
    </QueryClientProvider>
  );
}

export function useRepository(): Repository {
  const value = useContext(DataContext);
  if (!value) throw new Error('useRepository must be used inside DataProvider');
  return value.repository;
}

/** The local engine's own routes; undefined when the page talks to Azure. */
export function useEngine(): LocalEngineApi | undefined {
  const value = useContext(DataContext);
  if (!value) throw new Error('useEngine must be used inside DataProvider');
  return value.engine;
}
