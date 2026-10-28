'use client';

import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { ErrorNotice, SectionHeader, Surface } from '@/components/ui/primitives';
import type { LocalEngineApi } from '@/lib/api/local';
import styles from '../features.module.css';

const REFRESH_MS = 5000;

export function missingKey(item: string): 'language' | 'speech' | 'embedding' | 'other' {
  const lower = item.toLowerCase();
  if (lower.includes('language') || lower.includes('gguf')) return 'language';
  if (lower.includes('whisper') || lower.includes('speech') || lower.includes('sherpa')) return 'speech';
  if (lower.includes('embed') || lower.includes('e5')) return 'embedding';
  return 'other';
}

export function EngineSection({ engine }: { engine: LocalEngineApi }) {
  const { t } = useTranslation();
  const status = useQuery({
    queryKey: ['engine-status'],
    queryFn: () => engine.getStatus(),
    refetchInterval: REFRESH_MS,
  });
  return (
    <section className={styles.section}>
      <SectionHeader title={t('settings.engine.title')} />
      {status.isError ? (
        <ErrorNotice
          onRetry={() => {
            void status.refetch();
          }}
        />
      ) : null}
      {status.data ? (
        <Surface>
          <h3>{t('settings.engine.backend', { name: status.data.backend })}</h3>
          <p className={styles.muted}>
            {t('settings.engine.gpu', { value: t(status.data.gpu ? 'common.yes' : 'common.no') })}
          </p>
          <p className={styles.muted}>{t('settings.engine.jobs', { count: status.data.pendingJobs })}</p>
          {status.data.missing.length === 0 ? (
            <p className={styles.muted}>{t('settings.engine.ready')}</p>
          ) : (
            status.data.missing.map((item) => (
              <p key={item} className={styles.warningText}>
                {t(`settings.engine.missing.${missingKey(item)}`, { item })}
              </p>
            ))
          )}
        </Surface>
      ) : null}
    </section>
  );
}
