'use client';

import { useTranslation } from 'react-i18next';

import type { Graph } from '@/lib/domain/types';
import styles from '../features.module.css';
import { TYPE_ICONS } from './typeIcons';
import { radialLayout } from './layout';

const WIDTH = 640;
const HEIGHT = 420;
const NODE_RADIUS = 26;
export const MAP_MAX_NODES = 12;

interface GraphMapProps {
  graph: Graph;
  selected: string;
  onSelect(id: string): void;
}

// Secondary view: the list is the accessible primary. Every node is a real, focusable button.
export function GraphMap({ graph, selected, onSelect }: GraphMapProps) {
  const { t } = useTranslation();
  const layout = radialLayout(graph, selected, { width: WIDTH, height: HEIGHT, maxNodes: MAP_MAX_NODES });
  const byId = new Map(layout.nodes.map((node) => [node.id, node]));

  return (
    <div className={styles.section}>
      <p className={`${styles.muted} ${styles.small}`}>{t('graph.mapLabel')}</p>
      <div className={styles.mapFrame}>
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="group" aria-label={t('graph.viewMap')}>
          {layout.edges.map((edge) => {
            const from = byId.get(edge.from);
            const to = byId.get(edge.to);
            if (!from || !to) return null;
            const active = edge.from === selected || edge.to === selected;
            const classes = [
              active ? styles.edgeActive : styles.edge,
              edge.status === 'proposed' ? styles.edgeProposed : '',
            ].join(' ');
            return (
              <g key={edge.id}>
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} className={classes} />
                {edge.status === 'proposed' ? (
                  <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2} className={styles.edgeLabel}>
                    {t('graph.status.proposed')}
                  </text>
                ) : null}
              </g>
            );
          })}
          {layout.nodes.map((node) => {
            const Glyph = TYPE_ICONS[node.type];
            return (
              <g
                key={node.id}
                className={styles.mapNode}
                role="button"
                tabIndex={0}
                aria-pressed={node.id === selected}
                aria-label={`${node.label}, ${t(`graph.types.${node.type}`)}`}
                onClick={() => onSelect(node.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(node.id);
                  }
                }}
              >
                <circle cx={node.x} cy={node.y} r={NODE_RADIUS} />
                <Glyph x={node.x - 11} y={node.y - 11} size={22} aria-hidden />
                <text x={node.x} y={node.y + NODE_RADIUS + 14}>
                  {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
