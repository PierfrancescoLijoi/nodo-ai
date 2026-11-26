'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/shell/PageHeader';
import { Button, ErrorNotice, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import { useRepository } from '@/providers/data';
import { useQueryClient } from '@tanstack/react-query';
import { keys, useWikiPages, useWikiRevisions } from '@/lib/queries';

export function WikiScreen() {
  const pages = useWikiPages();
  const [selected, setSelected] = useState<string>();
  const revisions = useWikiRevisions(selected);
  const repository = useRepository();
  const client = useQueryClient();
  const selectedPage = pages.data?.find((page) => page.id === selected);
  const approve = async () => {
    if (!selected) return;
    await repository.approveWikiPage(selected);
    await Promise.all([
      client.invalidateQueries({ queryKey: keys.wikiPages }),
      client.invalidateQueries({ queryKey: keys.wikiRevisions(selected) }),
    ]);
  };
  return (
    <>
      <PageHeader title="Wiki" intro="Cited, reviewable knowledge pages compiled from your sources." />
      {pages.isPending ? <Skeleton size="tall" /> : null}
      {pages.isError ? <ErrorNotice onRetry={() => void pages.refetch()} /> : null}
      {pages.data?.length === 0 ? <p className="muted">No Wiki pages yet.</p> : null}
      <div className="split">
        <div className="stack">
          {pages.data?.map((page) => (
            <Surface key={page.id} as="article">
              <div className="spread">
                <h2>{page.subjectId}</h2>
                <Tag label={page.status} tone={page.status === 'stale' ? 'warning' : 'accent'} />
              </div>
              <p className="muted">
                {page.kind} · {page.currentRevision ? `Revision ${page.currentRevision}` : 'Draft'}
              </p>
              <Button variant="secondary" onClick={() => setSelected(page.id)}>
                Open page
              </Button>
            </Surface>
          ))}
        </div>
        <aside className="stack" aria-label="Page detail">
          {revisions.isPending ? <Skeleton size="card" /> : null}
          {revisions.isError ? <ErrorNotice onRetry={() => void revisions.refetch()} /> : null}
          {selectedPage && revisions.data ? (
            <Surface as="article">
              <div className="spread">
                <h2>{selectedPage.subjectId}</h2>
                <Tag label={selectedPage.status} tone="accent" />
              </div>
              {selectedPage.status === 'proposed' ? (
                <Button onClick={() => void approve()}>Approve proposal</Button>
              ) : null}
              {revisions.data[0] ? (
                <p>{revisions.data[0].body}</p>
              ) : (
                <p className="muted">No approved revision yet.</p>
              )}
            </Surface>
          ) : (
            <p className="muted">Select a Wiki page to inspect its revision.</p>
          )}
        </aside>
      </div>
    </>
  );
}
