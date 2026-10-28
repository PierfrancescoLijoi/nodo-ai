'use client';

import { useTranslation } from 'react-i18next';

import { Button, Surface } from '@/components/ui/primitives';
import { useAuth } from '@/providers/auth';
import styles from './shell.module.css';

export function Splash({ label }: { label: string }) {
  return (
    <div className={styles.center} role="status">
      <span className={styles.splash}>{label}</span>
    </div>
  );
}

export function NotConnected({ reason }: { reason: 'local' | 'cloud' }) {
  const { t } = useTranslation();
  return (
    <main className={styles.center}>
      <Surface raised className={styles.centerCard}>
        <h1>{t('web.notConnected.title')}</h1>
        <p>{t(reason === 'local' ? 'web.notConnected.local' : 'web.notConnected.cloud')}</p>
      </Surface>
    </main>
  );
}

export function SignInScreen() {
  const { t } = useTranslation();
  const auth = useAuth();
  return (
    <main className={styles.center}>
      <Surface raised className={styles.centerCard}>
        <h1>{t('login.title')}</h1>
        <p>{t('login.body')}</p>
        {auth.error ? <p role="alert">{t('login.error')}</p> : null}
        <div>
          <Button onClick={() => void auth.signIn(`${window.location.pathname}${window.location.search}`)}>
            {t('login.signIn')}
          </Button>
        </div>
      </Surface>
    </main>
  );
}
