'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { loadCloudConfig } from '@/lib/runtime/config';
import { resolveConnection, type Connection } from '@/lib/runtime/connection';
import { sessionStore } from '@/lib/runtime/storage';

export type ConnectionState = { status: 'loading' } | { status: 'ready'; connection: Connection };

const ConnectionContext = createContext<ConnectionState | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConnectionState>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    void (async () => {
      let connection: Connection;
      try {
        const config = await loadCloudConfig();
        connection = resolveConnection(config, {
          origin: window.location.origin,
          pathname: window.location.pathname,
          search: window.location.search,
          hash: window.location.hash,
          session: sessionStore(),
          replaceUrl: (url) => window.history.replaceState(null, '', url),
        });
      } catch {
        // A config file that exists but is wrong: say so, do not guess another mode.
        connection = { mode: 'none', reason: 'cloud' };
      }
      if (active) setState({ status: 'ready', connection });
    })();
    return () => {
      active = false;
    };
  }, []);

  return <ConnectionContext.Provider value={state}>{children}</ConnectionContext.Provider>;
}

export function useConnectionState(): ConnectionState {
  const value = useContext(ConnectionContext);
  if (!value) throw new Error('useConnectionState must be used inside ConnectionProvider');
  return value;
}

/** For screens that only render once the connection is known (everything behind the gate). */
export function useConnection(): Connection {
  const state = useConnectionState();
  if (state.status !== 'ready') throw new Error('The connection is not resolved yet');
  return state.connection;
}
