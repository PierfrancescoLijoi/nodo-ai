import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type DependencyList,
  type ReactNode,
} from 'react';

import { usePrefs } from '@/prefs/PrefsProvider';

import { createMockRepository } from './mock/mockRepository';
import type { MeetingsRepository } from './types';

const RepositoryContext = createContext<MeetingsRepository | null>(null);

// Swap createMockRepository for an HTTP adapter generated from packages/contracts once the API is deployed.
export function RepositoryProvider({ children }: { children: ReactNode }) {
  const { locale } = usePrefs();
  const repository = useMemo(() => createMockRepository(locale), [locale]);
  return <RepositoryContext.Provider value={repository}>{children}</RepositoryContext.Provider>;
}

export function useRepository(): MeetingsRepository {
  const value = useContext(RepositoryContext);
  if (!value) throw new Error('useRepository must be used inside RepositoryProvider');
  return value;
}

export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: Error };

export function useAsync<T>(
  load: (repository: MeetingsRepository) => Promise<T>,
  deps: DependencyList = [],
): AsyncState<T> {
  const repository = useRepository();
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });
  useEffect(() => {
    let cancelled = false;
    // Reset to loading when the inputs change; the cancel flag below prevents stale results.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ status: 'loading' });
    load(repository).then(
      (data) => {
        if (!cancelled) setState({ status: 'ready', data });
      },
      (error: unknown) => {
        if (!cancelled) setState({ status: 'error', error: error instanceof Error ? error : new Error(String(error)) });
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repository, ...deps]);
  return state;
}
