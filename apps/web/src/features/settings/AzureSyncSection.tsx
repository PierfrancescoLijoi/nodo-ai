'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Checkbox, SectionHeader, Surface, TextField } from '@/components/ui/primitives';
import type { LocalEngineApi, SyncProgress } from '@/lib/api/local';
import { buildAuthorizationUrl } from '@/lib/auth/oidc';
import type { AzureProfile } from '@/lib/runtime/connection';
import { localStore } from '@/lib/runtime/storage';
import styles from '../features.module.css';
import { isSyncTokenMessage, normalizeTarget, readTarget, saveTarget, SYNC_CHANNEL } from './syncTarget';

const POLL_MS = 2000;

/** Copies the archive to an Azure deployment (ADR-015). The Azure token is held in memory only. */
export function AzureSyncSection({ engine }: { engine: LocalEngineApi }) {
  const { t } = useTranslation();
  const [target, setTarget] = useState<AzureProfile>(() => readTarget(localStore()));
  const [accessToken, setAccessToken] = useState<string>();
  const [force, setForce] = useState(false);
  const [progress, setProgress] = useState<SyncProgress>();
  const [failed, setFailed] = useState(false);
  const [signInFailed, setSignInFailed] = useState(false);
  const valid = useMemo(() => normalizeTarget(target), [target]);
  const update = (key: keyof AzureProfile) => (value: string) =>
    setTarget((current) => ({ ...current, [key]: value }));

  // The token arrives from the pop-up's callback page over a same-origin channel.
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel(SYNC_CHANNEL);
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (isSyncTokenMessage(event.data)) setAccessToken(event.data.token);
    };
    return () => channel.close();
  }, []);

  useEffect(() => {
    if (progress?.state !== 'running') return;
    let active = true;
    const timer = setInterval(() => {
      engine.getSync().then(
        (next) => active && setProgress(next),
        () => active && setFailed(true),
      );
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [engine, progress?.state]);

  const signIn = async () => {
    if (!valid) return;
    setSignInFailed(false);
    try {
      saveTarget(localStore(), valid);
      const { url } = await buildAuthorizationUrl(
        { issuer: valid.issuer, clientId: valid.clientId, scope: valid.scope },
        'sync',
        '/settings/',
        { session: localStore(), origin: window.location.origin },
      );
      if (!window.open(url, 'nodo-sync-auth', 'popup,width=520,height=720')) setSignInFailed(true);
    } catch {
      setSignInFailed(true);
    }
  };

  const copy = () => {
    if (!valid || !accessToken) return;
    setFailed(false);
    engine
      .startSync({ cloudUrl: valid.apiBaseUrl, accessToken, force })
      .then(setProgress, () => setFailed(true));
  };

  return (
    <section className={styles.section}>
      <SectionHeader title={t('settings.sync.title')} />
      <Surface>
        <p className={styles.muted}>{t('settings.sync.body')}</p>
        <div className={styles.formGrid}>
          <TextField
            label={t('connection.azure.api')}
            value={target.apiBaseUrl}
            onChange={update('apiBaseUrl')}
            inputMode="url"
            autoComplete="off"
          />
          <TextField
            label={t('connection.azure.issuer')}
            value={target.issuer}
            onChange={update('issuer')}
            inputMode="url"
            autoComplete="off"
          />
          <TextField
            label={t('connection.azure.clientId')}
            value={target.clientId}
            onChange={update('clientId')}
            autoComplete="off"
          />
          <TextField
            label={t('connection.azure.scope')}
            value={target.scope}
            onChange={update('scope')}
            autoComplete="off"
          />
        </div>
        {!valid && (target.apiBaseUrl || target.issuer) ? (
          <p className={styles.small}>{t('connection.azure.validation')}</p>
        ) : null}
        <div className={styles.row}>
          <Button variant="secondary" disabled={!valid} onClick={() => void signIn()}>
            {t('settings.sync.signIn')}
          </Button>
          {accessToken ? <span className={styles.accentText}>{t('settings.sync.signedIn')}</span> : null}
        </div>
        {signInFailed ? (
          <p className={styles.danger} role="alert">
            {t('settings.sync.signInError')}
          </p>
        ) : null}
        <Checkbox checked={force} onChange={setForce} label={t('settings.sync.force')} />
        <div>
          <Button disabled={!accessToken || !valid || progress?.state === 'running'} onClick={copy}>
            {t('settings.sync.copy')}
          </Button>
        </div>
        {failed ? (
          <p className={styles.danger} role="alert">
            {t('settings.sync.error')}
          </p>
        ) : null}
        {progress ? (
          <div className={styles.stack} aria-live="polite">
            <h3>{t(`settings.sync.state.${progress.state}`)}</h3>
            <p>{t('settings.sync.counts.total', { count: progress.total })}</p>
            <p>{t('settings.sync.counts.copied', { count: progress.copied })}</p>
            <p>{t('settings.sync.counts.unchanged', { count: progress.unchanged })}</p>
            <p>{t('settings.sync.counts.deleted', { count: progress.deleted })}</p>
            {progress.failed.map((item) => (
              <p key={`${item.meetingId}-${item.reason}`} className={styles.danger}>
                {t('settings.sync.failedMeeting', { id: item.meetingId, reason: item.reason })}
              </p>
            ))}
          </div>
        ) : null}
      </Surface>
    </section>
  );
}
