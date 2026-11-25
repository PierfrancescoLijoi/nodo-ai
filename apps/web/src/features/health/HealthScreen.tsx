'use client';

import { PageHeader } from '@/components/shell/PageHeader';
import { ErrorNotice, Skeleton, Surface } from '@/components/ui/primitives';
import { useGraphInsights } from '@/lib/queries';

const GROUPS = [
  ['Isolated nodes', 'isolated'],
  ['Bridge nodes', 'bridges'],
  ['Orphan concepts', 'orphanConcepts'],
  ['Duplicate concepts', 'duplicateConcepts'],
  ['Contradicting decisions', 'contradictingDecisions'],
] as const;

export function HealthScreen() {
  const insights = useGraphInsights();
  return (
    <>
      <PageHeader title="Health" intro="Evidence and graph integrity findings for your workspace." />
      {insights.isPending ? <Skeleton size="tall" /> : null}
      {insights.isError ? <ErrorNotice onRetry={() => void insights.refetch()} /> : null}
      {insights.data ? (
        <div className="stack">
          {insights.data.truncated ? <p className="muted">Analysis is bounded; refine the graph for a full result.</p> : null}
          {GROUPS.map(([label, field]) => (
            <Surface key={field} as="section">
              <h2>{label}</h2>
              {insights.data[field].length ? (
                <ul>{insights.data[field].map((id) => <li key={id} className="mono">{id}</li>)}</ul>
              ) : <p className="muted">None found.</p>}
            </Surface>
          ))}
        </div>
      ) : null}
    </>
  );
}
