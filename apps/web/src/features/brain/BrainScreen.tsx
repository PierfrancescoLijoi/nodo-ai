'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice } from '@/components/ui/primitives';
import { useRepository } from '@/providers/data';
import { BrainExplorer } from './BrainExplorer';
import { mergeGraphs } from './filter';
import styles from './brain.module.css';

const BRAIN_KEY = ['brain'] as const;

export function BrainScreen() {
  const { t } = useTranslation();
  const repository = useRepository();
  const queryClient = useQueryClient();
  const graph = useQuery({
    queryKey: BRAIN_KEY,
    queryFn: () => repository.getGraph({ depth: 2, limit: 200 }),
  });
  const expansion = useMutation({
    mutationFn: (seedId: string) => repository.getGraph({ seedId, depth: 2, limit: 200 }),
    onSuccess: (result) => {
      queryClient.setQueryData(BRAIN_KEY, (current: typeof result | undefined) =>
        current ? mergeGraphs(current, result) : result,
      );
    },
  });

  return (
    <div className={styles.screen}>
      <PageHeader title={t('brain.title')} intro={t('brain.intro')} />
      {graph.isPending ? (
        <div className={styles.loading} role="status">
          <Brain size={48} weight="thin" aria-hidden />
          <div>
            <h2>{t('brain.loadingTitle')}</h2>
            <p>{t('brain.loadingBody')}</p>
          </div>
          <span className={styles.loadingBrain} aria-hidden />
        </div>
      ) : null}
      {graph.isError ? (
        <div className={styles.errorState}>
          <ErrorNotice onRetry={() => void graph.refetch()} />
          <p>{t('brain.errorBody')}</p>
        </div>
      ) : null}
      {graph.data?.nodes.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.emptyOrb} aria-hidden />
          <h2>{t('brain.emptyTitle')}</h2>
          <p>{t('brain.emptyBody')}</p>
          <Button variant="secondary" onClick={() => void graph.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      ) : null}
      {graph.data && graph.data.nodes.length > 0 ? (
        <>
          {graph.data.truncated ? <p className={styles.partial}>{t('brain.partial')}</p> : null}
          <BrainExplorer
            graph={graph.data}
            expanding={expansion.isPending}
            onExpand={(id) => expansion.mutate(id)}
          />
          {expansion.isError ? <p className={styles.expandError}>{t('brain.expandError')}</p> : null}
        </>
      ) : null}
    </div>
  );
}
