'use client';

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { AppShell } from '@/components/shell/AppShell';
import { NotConnected, SignInScreen, Splash } from '@/components/shell/Gate';
import { AuthProvider, useAuth } from './auth';
import { ConnectionProvider, useConnectionState } from './connection';
import { DataProvider } from './data';
import { PrefsProvider, usePrefs } from './prefs';

const CALLBACK_PATH = '/auth/callback';

function Gate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const auth = useAuth();
  const state = useConnectionState();
  const pathname = usePathname();

  // The provider returns here with a code; the callback page finishes the sign-in itself.
  if (pathname.startsWith(CALLBACK_PATH)) return <>{children}</>;
  if (state.status !== 'ready') return <Splash label={t('web.starting')} />;
  if (state.connection.mode === 'none') return <NotConnected reason={state.connection.reason} />;
  if (auth.status === 'loading') return <Splash label={t('web.starting')} />;
  if (auth.status === 'signedOut') return <SignInScreen />;
  return <AppShell connection={state.connection}>{children}</AppShell>;
}

function Resolved({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const prefs = usePrefs();
  const state = useConnectionState();
  if (!prefs.ready || state.status !== 'ready') return <Splash label={t('web.starting')} />;
  return (
    <AuthProvider connection={state.connection}>
      <DataProvider connection={state.connection}>
        <Gate>{children}</Gate>
      </DataProvider>
    </AuthProvider>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <PrefsProvider>
      <ConnectionProvider>
        <Resolved>{children}</Resolved>
      </ConnectionProvider>
    </PrefsProvider>
  );
}
