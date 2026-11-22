'use client';

import { ArrowCounterClockwise, CornersIn, CornersOut, MagnifyingGlass, X } from '@phosphor-icons/react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Segmented, Tag } from '@/components/ui/primitives';
import type { EntityType, Graph, RelationKind } from '@/lib/domain/types';
import { meetingHref } from '../meetings/MeetingRow';
import { BrainCanvas } from './BrainCanvas';
import { EMPTY_FILTERS, filterGraph, type ReviewStatus } from './filter';
import { brainLayout, type BrainNode } from './layout';
import styles from './brain.module.css';

const TYPES: EntityType[] = ['decision', 'topic', 'project', 'system', 'meeting', 'person', 'action'];
const KINDS: RelationKind[] = [
  'mentionedIn',
  'about',
  'decidedIn',
  'proposedBy',
  'supersedes',
  'assignedTo',
  'dependsOn',
  'contradicts',
];

function useMedia(query: string) {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  );
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}

interface BrainExplorerProps {
  graph: Graph;
  expanding?: boolean;
  onExpand(id: string): void;
}

export function BrainExplorer({ graph, expanding = false, onExpand }: BrainExplorerProps) {
  const { t } = useTranslation();
  const reducedMotion = useMedia('(prefers-reduced-motion: reduce)');
  const [view, setView] = useState<'list' | 'map'>(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches ? 'list' : 'map',
  );
  const [query, setQuery] = useState('');
  const [type, setType] = useState<EntityType | 'all'>('all');
  const [kind, setKind] = useState<RelationKind | 'all'>('all');
  const [status, setStatus] = useState<ReviewStatus | 'all'>('all');
  const [selectedId, setSelectedId] = useState<string>();
  const [focusedId, setFocusedId] = useState<string>();
  const [hovered, setHovered] = useState<{ node?: BrainNode; x: number; y: number }>({ x: 0, y: 0 });
  const [fullscreen, setFullscreen] = useState(false);
  const explorerRef = useRef<HTMLDivElement>(null);
  const typeLabel = useCallback((value: EntityType) => t(`graph.types.${value}`), [t]);
  const relationLabel = useCallback((value: RelationKind) => t(`brain.kinds.${value}`), [t]);

  useEffect(() => {
    const close = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedId(undefined);
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, []);

  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === explorerRef.current);
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);

  const filtered = useMemo(
    () =>
      filterGraph(graph, {
        ...EMPTY_FILTERS,
        query,
        types: type === 'all' ? new Set() : new Set([type]),
        kinds: kind === 'all' ? new Set() : new Set([kind]),
        statuses: status === 'all' ? new Set() : new Set([status]),
      }),
    [graph, kind, query, status, type],
  );
  const layout = useMemo(() => brainLayout(filtered, { width: 1200, height: 720 }), [filtered]);
  const selected = graph.nodes.find((node) => node.id === selectedId);
  const relations = selected
    ? graph.edges.filter((edge) => edge.from === selected.id || edge.to === selected.id)
    : [];
  const reset = () => {
    setFocusedId(undefined);
    setSelectedId(undefined);
  };
  const focus = (id: string) => {
    setFocusedId(id);
    setSelectedId(id);
    onExpand(id);
  };
  const flyFromSearch = () => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return;
    const match = graph.nodes.find((node) => node.label.toLocaleLowerCase().includes(needle));
    if (match) focus(match.id);
  };
  const toggleFullscreen = async () => {
    if (document.fullscreenElement) await document.exitFullscreen?.();
    else await explorerRef.current?.requestFullscreen?.();
  };

  return (
    <div ref={explorerRef} className={styles.explorer}>
      <div className={`${styles.toolbar} ${view === 'map' ? styles.toolbarOverlay : ''}`}>
        <form
          className={styles.search}
          onSubmit={(event) => {
            event.preventDefault();
            flyFromSearch();
          }}
        >
          <label htmlFor="brain-search">{t('brain.searchLabel')}</label>
          <div className={styles.searchControl}>
            <MagnifyingGlass size={19} aria-hidden />
            <input
              id="brain-search"
              value={query}
              placeholder={t('brain.searchPlaceholder')}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </form>
        <div className={styles.filters} aria-label={t('brain.filters')}>
          <label>
            <span>{t('brain.typeFilter')}</span>
            <select value={type} onChange={(event) => setType(event.target.value as EntityType | 'all')}>
              <option value="all">{t('brain.allTypes')}</option>
              {TYPES.map((value) => (
                <option key={value} value={value}>
                  {t(`graph.types.${value}`)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('brain.kindFilter')}</span>
            <select value={kind} onChange={(event) => setKind(event.target.value as RelationKind | 'all')}>
              <option value="all">{t('brain.allKinds')}</option>
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {t(`brain.kinds.${value}`)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('brain.statusFilter')}</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value as ReviewStatus | 'all')}
            >
              <option value="all">{t('brain.allStatuses')}</option>
              <option value="approved">{t('graph.status.approved')}</option>
              <option value="proposed">{t('graph.status.proposed')}</option>
            </select>
          </label>
        </div>
        <Segmented<'list' | 'map'>
          label={t('brain.viewLabel')}
          value={view}
          onChange={setView}
          options={[
            { value: 'list', label: t('graph.viewList') },
            { value: 'map', label: t('graph.viewMap') },
          ]}
        />
      </div>

      <div className={styles.breadcrumb} aria-label={t('brain.location')}>
        <button type="button" onClick={reset} aria-current={focusedId ? undefined : 'page'}>
          {t('brain.wholeBrain')}
        </button>
        {focusedId ? <span aria-hidden>/</span> : null}
        {focusedId ? <span>{graph.nodes.find((node) => node.id === focusedId)?.label}</span> : null}
        {focusedId ? (
          <Button variant="ghost" icon={ArrowCounterClockwise} onClick={reset}>
            {t('brain.backToWhole')}
          </Button>
        ) : null}
      </div>

      <p className="visually-hidden" aria-live="polite">
        {t('brain.resultCount', { count: filtered.nodes.length })}
      </p>

      {filtered.nodes.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.emptyOrb} aria-hidden />
          <h2>{t('brain.noMatchTitle')}</h2>
          <p>{t('brain.noMatchBody')}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setQuery('');
              setType('all');
              setKind('all');
              setStatus('all');
            }}
          >
            {t('brain.clearFilters')}
          </Button>
        </div>
      ) : view === 'map' ? (
        <div className={styles.mapShell} data-testid="brain-map-shell" data-immersive="true">
          <BrainCanvas
            layout={layout}
            selectedId={selectedId}
            focusedId={focusedId}
            hoveredId={hovered.node?.id}
            reducedMotion={reducedMotion}
            mapNodesLabel={t('brain.mapNodes')}
            typeLabel={typeLabel}
            relationLabel={relationLabel}
            onSelect={setSelectedId}
            onFocus={focus}
            onHover={setHovered}
          />
          <button
            type="button"
            className={styles.fullscreen}
            aria-label={fullscreen ? t('brain.exitFullscreen') : t('brain.enterFullscreen')}
            onClick={() => void toggleFullscreen()}
          >
            {fullscreen ? <CornersIn size={20} aria-hidden /> : <CornersOut size={20} aria-hidden />}
          </button>
          <div className={styles.mapHint}>{t('brain.mapHint')}</div>
          <div className={styles.legend} aria-label={t('brain.legend')}>
            <span className={styles.legendNode} aria-hidden /> {t('brain.legendNeuron')}
            <span className={styles.legendLine} aria-hidden /> {t('graph.status.approved')}
            <span className={styles.legendDashed} aria-hidden /> {t('graph.status.proposed')}
          </div>
          {hovered.node ? (
            <div
              className={styles.tooltip}
              role="tooltip"
              data-testid="brain-tooltip"
              style={{ left: Math.min(hovered.x + 18, 760), top: Math.max(90, hovered.y - 24) }}
            >
              <strong>{hovered.node.label}</strong>
              <span>{t(`graph.types.${hovered.node.type}`)}</span>
            </div>
          ) : null}
        </div>
      ) : (
        <div className={styles.listView}>
          {TYPES.map((group) => {
            const nodes = filtered.nodes.filter((node) => node.type === group);
            if (nodes.length === 0) return null;
            return (
              <section key={group} aria-labelledby={`brain-type-${group}`}>
                <div className={styles.listHeading}>
                  <h2 id={`brain-type-${group}`}>{t(`graph.types.${group}`)}</h2>
                  <span>{nodes.length}</span>
                </div>
                <ul>
                  {nodes.map((node) => {
                    const count = filtered.edges.filter(
                      (edge) => edge.from === node.id || edge.to === node.id,
                    ).length;
                    return (
                      <li key={node.id}>
                        <button type="button" onClick={() => setSelectedId(node.id)}>
                          <span className={styles.listGlyph} data-type={node.type} aria-hidden />
                          <span>
                            <strong>{node.label}</strong>
                            <small>{t('brain.connectionCount', { count })}</small>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      {selected ? (
        <aside
          className={`${styles.detail} ${view === 'map' ? styles.detailMap : ''}`}
          aria-label={t('brain.nodeDetails')}
        >
          <div className={styles.detailTop}>
            <div>
              <Tag label={t(`graph.types.${selected.type}`)} tone="accent" />
              <h2>{selected.label}</h2>
            </div>
            <button
              type="button"
              className={styles.close}
              aria-label={t('common.close')}
              onClick={() => setSelectedId(undefined)}
            >
              <X size={20} aria-hidden />
            </button>
          </div>
          <Button variant="secondary" onClick={() => focus(selected.id)} disabled={expanding}>
            {expanding ? t('brain.expanding') : t('brain.focusNode')}
          </Button>
          <div className={styles.detailSection}>
            <h3>{t('brain.connections')}</h3>
            {relations.length === 0 ? <p>{t('graph.noRelations')}</p> : null}
            {KINDS.map((relationKind) => {
              const items = relations.filter((edge) => edge.kind === relationKind);
              if (items.length === 0) return null;
              return (
                <section key={relationKind}>
                  <h3>{t(`brain.kinds.${relationKind}`)}</h3>
                  <ul>
                    {items.map((edge) => {
                      const otherId = edge.from === selected.id ? edge.to : edge.from;
                      const other = graph.nodes.find((node) => node.id === otherId);
                      return (
                        <li key={edge.id}>
                          <button type="button" onClick={() => other && setSelectedId(other.id)}>
                            {other?.label ?? otherId}
                          </button>
                          <span
                            className={
                              edge.status === 'proposed' ? styles.proposedStatus : styles.approvedStatus
                            }
                          >
                            {edge.status ? t(`graph.status.${edge.status}`) : t('brain.statusUnknown')}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>
          <div className={styles.detailSection}>
            <h3>{t('brain.mentionedIn')}</h3>
            {[
              ...new Set(
                [selected.meetingId, ...relations.flatMap((edge) => edge.meetingIds ?? [])].filter(Boolean),
              ),
            ].map((meetingId) => (
              <Link key={meetingId} href={meetingHref(meetingId as string)}>
                {t('graph.openSource')}
              </Link>
            ))}
            {!selected.meetingId && relations.every((edge) => !edge.meetingIds?.length) ? (
              <p>{t('brain.noSources')}</p>
            ) : null}
          </div>
          {relations.some((edge) => edge.status === 'proposed') ? (
            <Link href="/review/">{t('brain.openReview')}</Link>
          ) : null}
        </aside>
      ) : null}
    </div>
  );
}
