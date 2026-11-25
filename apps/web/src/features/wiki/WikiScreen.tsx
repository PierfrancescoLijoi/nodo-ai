'use client';

import { PageHeader } from '@/components/shell/PageHeader';
import { ErrorNotice, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import { useWikiPages } from '@/lib/queries';

export function WikiScreen() {
  const pages = useWikiPages();
  return (
    <>
      <PageHeader title="Wiki" intro="Cited, reviewable knowledge pages compiled from your sources." />
      {pages.isPending ? <Skeleton size="tall" /> : null}
      {pages.isError ? <ErrorNotice onRetry={() => void pages.refetch()} /> : null}
      {pages.data?.length === 0 ? <p className="muted">No Wiki pages yet.</p> : null}
      <div className="stack">
        {pages.data?.map((page) => (
          <Surface key={page.id} as="article">
            <div className="spread"><h2>{page.subjectId}</h2><Tag label={page.status} tone={page.status === 'stale' ? 'warning' : 'accent'} /></div>
            <p className="muted">{page.kind} · {page.currentRevision ? `Revision ${page.currentRevision}` : 'Draft'}</p>
          </Surface>
        ))}
      </div>
    </>
  );
}
