'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, SectionHeader, Surface } from '@/components/ui/primitives';
import type { IndexMode, IndexProgress, LocalEngineApi } from '@/lib/api/local';
import styles from '../features.module.css';

const POLL_MS = 2000;
const SHOWN_FINDINGS = 20;

/** Checks that search and the graph match the archive, and rebuilds what does not (ADR-021). */
export function IndexSection({ engine }: { engine: LocalEngineApi }) {
  const { t } = useTranslation();
  const [progress, setProgress] = useState<IndexProgress>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (progress?.state !== 'running') return;
    let active = true;
    const timer = setInterval(() => {
      engine.getIndex().then(
        (next) => active && setProgress(next),
        () => active && setFailed(true),
      );
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [engine, progress?.state]);

  const run = (mode: IndexMode) => {
    setFailed(false);
    engine.startIndex(mode).then(setProgress, () => setFailed(true));
  };

  const running = progress?.state === 'running';
  const found = progress?.findings.length ?? 0;
  const done = progress?.state === 'done';

  return (
    <section className={styles.section}>
      <SectionHeader title={t('settings.index.title')} />
      <Surface>
        <p className={styles.muted}>{t('settings.index.body')}</p>
        <div className={styles.row}>
          <Button variant="secondary" disabled={running} onClick={() => run('verify')}>
            {t('settings.index.check')}
          </Button>
          <Button
            disabled={running || (done && progress?.mode === 'verify' && found === 0)}
            onClick={() => run('repair')}
          >
            {t('settings.index.repair')}
          </Button>
        </div>
        {failed ? (
          <p className={styles.danger} role="alert">
            {t('settings.index.error')}
          </p>
        ) : null}
        {progress ? (
          <div className={styles.stack} aria-live="polite">
            <h3>{t(`settings.index.state.${progress.state}`)}</h3>
            {done && progress.mode === 'verify' ? (
              <p>{found === 0 ? t('settings.index.ok') : t('settings.index.findings', { count: found })}</p>
            ) : null}
            {done && progress.mode === 'repair' ? (
              <p>{t('settings.index.repaired', { count: progress.repaired })}</p>
            ) : null}
            {progress.findings.slice(0, SHOWN_FINDINGS).map((finding) => (
              <p key={`${finding.kind}-${finding.meetingId}`} className={styles.muted}>
                {t(`settings.index.kinds.${finding.kind}`)}
              </p>
            ))}
            {progress.failed.map((item) => (
              <p key={`${item.meetingId}-${item.reason}`} className={styles.danger}>
                {t('settings.index.failedMeeting', { id: item.meetingId, reason: item.reason })}
              </p>
            ))}
          </div>
        ) : null}
      </Surface>
    </section>
  );
}
