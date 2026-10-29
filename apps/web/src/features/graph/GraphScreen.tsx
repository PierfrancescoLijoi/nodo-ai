'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/shell/PageHeader';
import { ErrorNotice, LinkButton, Segmented, Skeleton, Surface, Tag } from '@/components/ui/primitives';
import type { EntityType, Graph } from '@/lib/domain/types';
import { useGraph, useReviews } from '@/lib/queries';
import styles from '../features.module.css';
import { meetingHref } from '../meetings/MeetingRow';
import { GraphMap, MAP_MAX_NODES } from './GraphMap';
import { radialLayout } from './layout';
import { TYPE_ICONS } from './typeIcons';

const TYPE_ORDER: EntityType[] = ['decision', 'topic', 'project', 'system', 'meeting', 'person', 'action'];

function ReviewLink() {
  const { t } = useTranslation();
  const reviews = useReviews();
  if (!reviews.data?.length) return null;
  return (
    <Surface tone="accent">
      <h2>{t('review.pending', { count: reviews.data.length })}</h2>
      <div>
        <LinkButton href="/review/" variant="secondary">
          {t('review.open')}
        </LinkButton>
      </div>
    </Surface>
  );
}

function NodeDetail({ graph, nodeId }: { graph: Graph; nodeId: string }) {
  const { t } = useTranslation();
  const node = graph.nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return null;
  const labelOf = (id: string) => graph.nodes.find((candidate) => candidate.id === id)?.label ?? id;
  const relations = graph.edges.filter((edge) => edge.from === nodeId || edge.to === nodeId);

  return (
    <Surface>
      <div className={styles.spread}>
        <h3>{node.label}</h3>
        <Tag label={t(`graph.types.${node.type}`)} tone="accent" />
      </div>
      <p className={styles.label}>{t('graph.relations', { count: relations.length })}</p>
      {relations.length === 0 ? <p className={styles.small}>{t('graph.noRelations')}</p> : null}
      <ul className={styles.list}>
        {relations.map((edge) => {
          const sentence = t(`graph.relation.${edge.kind}`, {
            from: labelOf(edge.from),
            to: labelOf(edge.to),
          });
          return (
            <li key={edge.id} className={styles.small}>
              {edge.status ? `${sentence} (${t(`graph.status.${edge.status}`)})` : sentence}
            </li>
          );
        })}
      </ul>
      {node.meetingId ? (
        <div>
          <Link href={meetingHref(node.meetingId)}>{t('graph.openSource')}</Link>
        </div>
      ) : null}
    </Surface>
  );
}

function EntityList({
  graph,
  selected,
  onSelect,
}: {
  graph: Graph;
  selected: string;
  onSelect(id: string): void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.stack}>
      {TYPE_ORDER.map((type) => {
        const nodes = graph.nodes.filter((node) => node.type === type);
        if (nodes.length === 0) return null;
        const Glyph = TYPE_ICONS[type];
        return (
          <section key={type} className={styles.typeGroup} aria-label={t(`graph.types.${type}`)}>
            <h3 className={styles.label}>{t(`graph.types.${type}`)}</h3>
            {nodes.map((node) => (
              <div key={node.id} className={styles.typeGroup}>
                <button
                  type="button"
                  className={styles.entity}
                  aria-pressed={node.id === selected}
                  onClick={() => onSelect(node.id)}
                >
                  <Glyph size={20} aria-hidden />
                  {node.label}
                </button>
                {node.id === selected ? <NodeDetail graph={graph} nodeId={node.id} /> : null}
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}

export function GraphScreen() {
  const { t } = useTranslation();
  const [seedId, setSeedId] = useState<string>();
  const graph = useGraph(seedId);
  const [view, setView] = useState<'list' | 'map'>('list');
  const [selected, setSelected] = useState<string>();

  const data = graph.data;
  const effective = data
    ? data.nodes.some((node) => node.id === selected)
      ? (selected as string)
      : (data.nodes[0]?.id ?? '')
    : '';
  const select = (id: string) => {
    setSelected(id);
    setSeedId(id);
  };
  const partial =
    data !== undefined &&
    (data.truncated ||
      (view === 'map' &&
        radialLayout(data, effective, { width: 640, height: 420, maxNodes: MAP_MAX_NODES }).leftOut.length >
          0));

  return (
    <>
      <PageHeader title={t('graph.title')} intro={t('graph.intro')} />
      <ReviewLink />
      <Segmented<'list' | 'map'>
        label={t('graph.viewLabel')}
        value={view}
        onChange={setView}
        options={[
          { value: 'list', label: t('graph.viewList') },
          { value: 'map', label: t('graph.viewMap') },
        ]}
      />
      {graph.isError ? (
        <ErrorNotice
          onRetry={() => {
            void graph.refetch();
          }}
        />
      ) : null}
      {graph.isPending ? <Skeleton size="tall" /> : null}
      {data ? (
        <>
          {partial ? <p className={styles.muted}>{t('graph.truncated')}</p> : null}
          {data.nodes.length === 0 ? <p className={styles.muted}>{t('graph.noRelations')}</p> : null}
          {view === 'list' ? (
            <EntityList graph={data} selected={effective} onSelect={select} />
          ) : (
            <>
              <GraphMap graph={data} selected={effective} onSelect={select} />
              <NodeDetail graph={data} nodeId={effective} />
            </>
          )}
        </>
      ) : null}
    </>
  );
}
