'use client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import { useEngine } from '@/providers/data';
import styles from '../features.module.css';

export function ModelsScreen() {
  const { t } = useTranslation();
  const engine = useEngine();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<string>();
  const catalog = useQuery({
    queryKey: ['local-models'],
    queryFn: () => engine!.getModels(),
    enabled: !!engine,
  });
  return (
    <>
      <PageHeader title={t('models.title')} intro={t('models.intro')} />
      {!engine ? (
        <Surface tone="warning">
          <p>{t('models.localOnly')}</p>
        </Surface>
      ) : null}
      {catalog.isPending ? <Skeleton size="tall" /> : null}
      {catalog.isError ? <ErrorNotice onRetry={() => void catalog.refetch()} /> : null}
      {catalog.data ? (
        <>
          <p className={styles.muted}>{t('models.hardware', { tier: catalog.data.hardwareTier })}</p>
          <div className={styles.stack}>
            {catalog.data.models.map((model) => (
              <Surface key={model.id} as="article">
                <div className={styles.spread}>
                  <div>
                    <h2>{model.name}</h2>
                    <p className={styles.muted}>
                      {model.publisher} · {model.version} · {model.sizeMb} MB
                    </p>
                  </div>
                  <Tag
                    label={model.installed ? t('models.installed') : t('models.available')}
                    tone={model.installed ? 'accent' : 'default'}
                  />
                </div>
                <p>
                  {model.task} · {model.licence}
                </p>
                <a href={model.licenceUrl} target="_blank" rel="noreferrer">
                  {t('models.licence')}
                </a>
                <p className={styles.small}>
                  {model.sha256 ? `SHA-256: ${model.sha256}` : t('models.unverified')}
                </p>
                {model.installed && model.installable ? (
                  <Button variant="danger" onClick={() => setRemoving(model.id)}>
                    {t('models.remove')}
                  </Button>
                ) : null}
              </Surface>
            ))}
          </div>
          <ConfirmDialog
            open={!!removing}
            title={t('models.removeTitle')}
            confirmLabel={t('models.remove')}
            onCancel={() => setRemoving(undefined)}
            onConfirm={() => {
              if (!removing || !engine) return;
              void engine
                .removeModel(removing)
                .then(() => queryClient.invalidateQueries({ queryKey: ['local-models'] }))
                .finally(() => setRemoving(undefined));
            }}
          >
            <p>{t('models.removeBody')}</p>
          </ConfirmDialog>
        </>
      ) : null}
    </>
  );
}
