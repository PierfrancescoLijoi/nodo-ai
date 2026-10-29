'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Surface } from '@/components/ui/primitives';
import { completeAuthorization } from '@/lib/auth/oidc';
import { localStore, sessionStore } from '@/lib/runtime/storage';
import { useAuth } from '@/providers/auth';
import styles from '@/components/shell/shell.module.css';
import { SYNC_CHANNEL, type SyncTokenMessage } from '../settings/syncTarget';

/** Only a path inside this app: a crafted return address must not become an open redirect. */
export function safeReturnTo(value: string): string {
  return value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : '/';
}

/** Where the identity provider sends the browser back to (redirect URI: /auth/callback/). */
export function CallbackScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const auth = useAuth();
  const [failed, setFailed] = useState(false);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return; // the authorization code is single-use, so the exchange must run once
    ran.current = true;
    completeAuthorization(new URL(window.location.href), {
      session: sessionStore(),
      shared: localStore(),
      origin: window.location.origin,
    }).then(
      ({ tokens, returnTo, purpose }) => {
        if (purpose === 'sync') {
          const channel = new BroadcastChannel(SYNC_CHANNEL);
          const message: SyncTokenMessage = { type: 'nodo:sync-token', token: tokens.accessToken };
          channel.postMessage(message);
          channel.close();
          window.close();
          router.replace(safeReturnTo(returnTo)); // a window that cannot be closed goes back to the app
          return;
        }
        auth.acceptTokens(tokens);
        router.replace(safeReturnTo(returnTo));
      },
      () => setFailed(true),
    );
  }, [auth, router]);

  return (
    <main className={styles.center}>
      <Surface raised className={styles.centerCard}>
        {failed ? (
          <>
            <h1>{t('web.callback.failed')}</h1>
            <div>
              <Button onClick={() => router.replace('/')}>{t('web.callback.back')}</Button>
            </div>
          </>
        ) : (
          <p role="status">{t('web.callback.working')}</p>
        )}
      </Surface>
    </main>
  );
}
