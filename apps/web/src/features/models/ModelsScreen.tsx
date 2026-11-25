'use client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import { useEngine } from '@/providers/data';
import styles from '../features.module.css';

export function ModelsScreen() {
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
      <PageHeader
        title="Models"
        intro="Local model requirements, licences and verified installation status."
      />
      {!engine ? (
        <Surface tone="warning">
          <p>Model management is available only on the local engine.</p>
        </Surface>
      ) : null}
      {catalog.isPending ? <Skeleton size="tall" /> : null}
      {catalog.isError ? <ErrorNotice onRetry={() => void catalog.refetch()} /> : null}
      {catalog.data ? (
        <>
          <p className={styles.muted}>Hardware profile: {catalog.data.hardwareTier}</p>
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
                    label={model.installed ? 'Installed' : 'Available'}
                    tone={model.installed ? 'accent' : 'default'}
                  />
                </div>
                <p>
                  {model.task} · {model.licence}
                </p>
                <a href={model.licenceUrl} target="_blank" rel="noreferrer">
                  View licence
                </a>
                <p className={styles.small}>
                  {model.sha256
                    ? `SHA-256: ${model.sha256}`
                    : 'No immutable checksum: in-app installation is disabled.'}
                </p>
                {model.installed && model.installable ? (
                  <Button variant="danger" onClick={() => setRemoving(model.id)}>
                    Remove model
                  </Button>
                ) : null}
              </Surface>
            ))}
          </div>
          <ConfirmDialog
            open={!!removing}
            title="Remove local model?"
            confirmLabel="Remove"
            onCancel={() => setRemoving(undefined)}
            onConfirm={() => {
              if (!removing || !engine) return;
              void engine
                .removeModel(removing)
                .then(() => queryClient.invalidateQueries({ queryKey: ['local-models'] }))
                .finally(() => setRemoving(undefined));
            }}
          >
            <p>This permanently removes the selected model files from this computer.</p>
          </ConfirmDialog>
        </>
      ) : null}
    </>
  );
}
