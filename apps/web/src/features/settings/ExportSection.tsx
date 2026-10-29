'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, SectionHeader, Surface } from '@/components/ui/primitives';
import type { ExportJob } from '@/lib/domain/types';
import { useRepository } from '@/providers/data';
import styles from '../features.module.css';

const POLL_MS = 5000;

/** Saves a downloaded blob through a temporary link; the object URL is released at once. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** GDPR access and portability (ADR-019): everything the user owns, as one JSON file. */
export function ExportSection() {
  const { t } = useTranslation();
  const repository = useRepository();
  const [job, setJob] = useState<ExportJob>();
  const [failed, setFailed] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return;
    let active = true;
    const timer = setInterval(() => {
      repository.getExport(job.id).then(
        (next) => active && setJob(next),
        () => active && setFailed(true),
      );
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job, repository]);

  const start = () => {
    setFailed(false);
    setJob(undefined);
    repository.startExport().then(setJob, () => setFailed(true));
  };

  const download = () => {
    if (!job) return;
    setDownloading(true);
    setFailed(false);
    repository
      .downloadExport(job.id)
      .then((blob) => saveBlob(blob, 'nodo-export.json'))
      .catch(() => setFailed(true))
      .finally(() => setDownloading(false));
  };

  const preparing = job?.status === 'queued' || job?.status === 'running';
  return (
    <section className={styles.section}>
      <SectionHeader title={t('settings.data')} />
      <Surface>
        <h3>{t('settings.export.title')}</h3>
        <p className={`${styles.muted} ${styles.small}`}>{t('settings.export.body')}</p>
        <div className={styles.row}>
          <Button variant="secondary" disabled={preparing} onClick={start}>
            {t('settings.export.start')}
          </Button>
          {job?.status === 'ready' ? (
            <Button disabled={downloading} onClick={download}>
              {t('settings.export.ready')}
            </Button>
          ) : null}
        </div>
        <div aria-live="polite">
          {preparing ? <p className={styles.muted}>{t('settings.export.running')}</p> : null}
          {failed || job?.status === 'failed' ? (
            <p className={styles.danger} role="alert">
              {t('settings.export.failed')}
            </p>
          ) : null}
        </div>
      </Surface>
    </section>
  );
}
