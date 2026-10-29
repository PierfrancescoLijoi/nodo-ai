import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import {
  normalizeAzureProfile,
  AZURE_PROFILE_KEY,
  browserEnvironment,
  resolveConnection,
  safeRemove,
  safeSet,
} from './resolveConnection';
import type { AzureProfile, ResolvedConnection } from './types';

interface ConnectionValue {
  connection: ResolvedConnection;
  chooseDemo(): void;
  connectAzure(profile: AzureProfile): boolean;
  forgetAzure(): void;
}

const ConnectionContext = createContext<ConnectionValue | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const environment = useMemo(() => browserEnvironment(), []);
  const [connection, setConnection] = useState<ResolvedConnection>(() => resolveConnection(process.env, environment));
  const chooseDemo = useCallback(() => setConnection({ mode: 'mock' }), []);
  const connectAzure = useCallback(
    (candidate: AzureProfile) => {
      const profile = normalizeAzureProfile(candidate);
      if (!profile) return false;
      safeSet(environment?.localStorage, AZURE_PROFILE_KEY, JSON.stringify(profile));
      setConnection({ mode: 'azure', profile });
      return true;
    },
    [environment],
  );
  const forgetAzure = useCallback(() => {
    safeRemove(environment?.localStorage, AZURE_PROFILE_KEY);
    setConnection({ mode: 'setup' });
  }, [environment]);
  const value = useMemo(
    () => ({ connection, chooseDemo, connectAzure, forgetAzure }),
    [connection, forgetAzure, connectAzure, chooseDemo],
  );
  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

export function useConnection(): ConnectionValue {
  const value = useContext(ConnectionContext);
  if (!value) throw new Error('useConnection must be used inside ConnectionProvider');
  return value;
}
