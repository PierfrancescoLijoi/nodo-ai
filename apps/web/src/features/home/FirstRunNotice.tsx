'use client';

import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { LinkButton, Surface } from '@/components/ui/primitives';
import { useEngine } from '@/providers/data';
import styles from '../features.module.css';

/** First-run guidance (FR-26): on the local engine, missing models lead to the Models screen. */
export function FirstRunNotice() {
  const { t } = useTranslation();
  const engine = useEngine();
  const status = useQuery({
    queryKey: ['engine-status'],
    queryFn: () => engine!.getStatus(),
    enabled: Boolean(engine),
  });
  if (!status.data || status.data.missing.length === 0) return null;
  return (
    <Surface tone="accent" as="section" aria-labelledby="first-run-title">
      <h2 id="first-run-title">{t('home.firstRun.title')}</h2>
      <p className={styles.muted}>{t('home.firstRun.body', { count: status.data.missing.length })}</p>
      <div>
        <LinkButton href="/models/">{t('home.firstRun.cta')}</LinkButton>
      </div>
    </Surface>
  );
}
