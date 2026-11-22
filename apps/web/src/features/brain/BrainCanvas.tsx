'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { EntityType, RelationKind } from '@/lib/domain/types';
import { fitCamera, panCamera, zoomCamera, type Camera, type Point } from './camera';
import type { BrainLayout, BrainNode } from './layout';
import styles from './brain.module.css';

interface HoverPosition extends Point {
  node?: BrainNode;
}
interface Props {
  layout: BrainLayout;
  selectedId?: string;
  focusedId?: string;
  hoveredId?: string;
  reducedMotion: boolean;
  mapNodesLabel: string;
  typeLabel(type: EntityType): string;
  relationLabel(kind: RelationKind): string;
  onSelect(id: string): void;
  onFocus(id: string): void;
  onHover(position: HoverPosition): void;
}
interface Projected extends Point {
  id: string;
  radius: number;
}
const COLORS: Record<EntityType, string> = {
  decision: '#dc579e',
  topic: '#55bd77',
  project: '#dc579e',
  system: '#5b9be5',
  meeting: '#5b9be5',
  person: '#5ec5b3',
  action: '#e8b965',
};
const css = (el: Element, name: string, fallback: string) =>
  getComputedStyle(el).getPropertyValue(name).trim() || fallback;

export function BrainCanvas({
  layout,
  selectedId,
  focusedId,
  hoveredId,
  reducedMotion,
  mapNodesLabel,
  typeLabel,
  relationLabel,
  onSelect,
  onFocus,
  onHover,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cameraRef = useRef<Camera>({ x: 0, y: 0, zoom: 1, rotation: 0 });
  const pointsRef = useRef<Projected[]>([]);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | undefined>(undefined);
  const redrawRef = useRef<() => void>(() => undefined);
  const nodeIds = useMemo(() => layout.nodes.map((node) => node.id), [layout.nodes]);
  const [keyboardId, setKeyboardId] = useState(() => nodeIds[0] ?? '');
  const hit = useCallback(
    (x: number, y: number) =>
      pointsRef.current.find(
        (point) => Math.hypot(point.x - x, point.y - y) <= Math.max(12, point.radius + 5),
      )?.id,
    [],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    const frame = canvas?.parentElement;
    const context = canvas?.getContext('2d');
    if (!canvas || !frame || !context) return;
    let animation = 0;
    let initialized = false;
    const draw = (now = performance.now()) => {
      const bounds = frame.getBoundingClientRect();
      const width = Math.max(1, bounds.width),
        height = Math.max(1, bounds.height);
      context.clearRect(0, 0, width, height);
      context.fillStyle = css(canvas, '--brain-bg', '#0b1526');
      context.fillRect(0, 0, width, height);
      context.strokeStyle = 'rgb(80 100 121 / 0.1)';
      context.lineWidth = 1;
      for (let x = 0; x < width; x += 48) {
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, height);
        context.stroke();
      }
      for (let y = 0; y < height; y += 48) {
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(width, y);
        context.stroke();
      }
      const camera = cameraRef.current;
      const points = layout.nodes.map((node) => ({
        id: node.id,
        x: camera.x + node.x * camera.zoom,
        y: camera.y + node.y * camera.zoom,
        radius: Math.max(3, node.radius * camera.zoom),
      }));
      pointsRef.current = points;
      const byId = new Map(points.map((point) => [point.id, point]));
      const emphasis = hoveredId ?? selectedId ?? focusedId;
      const neighbours = new Set<string>();
      for (const edge of layout.edges) {
        if (edge.from === emphasis) neighbours.add(edge.to);
        if (edge.to === emphasis) neighbours.add(edge.from);
      }
      for (const edge of layout.edges) {
        const from = byId.get(edge.from),
          to = byId.get(edge.to);
        if (!from || !to) continue;
        const active = edge.from === emphasis || edge.to === emphasis;
        context.globalAlpha = emphasis && !active ? 0.08 : active ? 0.9 : 0.3;
        context.strokeStyle = edge.status === 'proposed' ? '#e8b965' : active ? '#86dacc' : '#506479';
        context.lineWidth = active ? 1.6 : 0.7;
        context.setLineDash(edge.status === 'proposed' ? [4, 5] : []);
        context.beginPath();
        context.moveTo(from.x, from.y);
        context.lineTo(to.x, to.y);
        context.stroke();
        if (focusedId && active) {
          const label = relationLabel(edge.kind),
            x = (from.x + to.x) / 2,
            y = (from.y + to.y) / 2;
          context.setLineDash([]);
          context.globalAlpha = 0.94;
          context.font = `600 10px ${css(canvas, '--font-sans', 'sans-serif')}`;
          const w = context.measureText(label).width + 12;
          context.fillStyle = '#111d2c';
          context.fillRect(x - w / 2, y - 9, w, 18);
          context.fillStyle = '#e5edf6';
          context.textAlign = 'center';
          context.fillText(label, x, y + 3);
        }
      }
      context.setLineDash([]);
      for (const node of layout.nodes) {
        const point = byId.get(node.id);
        if (!point) continue;
        const active = node.id === emphasis,
          neighbour = neighbours.has(node.id),
          color = COLORS[node.type];
        context.globalAlpha = emphasis && !active && !neighbour ? 0.15 : 1;
        const radius = active ? point.radius * 1.35 : point.radius;
        if (active) {
          context.fillStyle = `${color}30`;
          context.beginPath();
          context.arc(point.x, point.y, radius + 8, 0, Math.PI * 2);
          context.fill();
        }
        context.fillStyle = color;
        context.beginPath();
        context.arc(point.x, point.y, radius, 0, Math.PI * 2);
        context.fill();
        context.strokeStyle = active ? '#e5edf6' : '#0b1526';
        context.lineWidth = active ? 1.8 : 0.8;
        context.stroke();
        if (active || camera.zoom > 1.18) {
          context.fillStyle = '#e5edf6';
          context.font = `500 ${active ? 12 : 10}px ${css(canvas, '--font-sans', 'sans-serif')}`;
          context.textAlign = 'center';
          const label = node.label.length > 28 ? `${node.label.slice(0, 27)}…` : node.label;
          context.fillText(label, point.x, point.y + radius + 15);
        }
      }
      context.globalAlpha = 1;
      if (!reducedMotion && emphasis) {
        const point = byId.get(emphasis);
        if (point) {
          const phase = (now % 1800) / 1800;
          context.globalAlpha = 0.3 * (1 - phase);
          context.strokeStyle = '#86dacc';
          context.beginPath();
          context.arc(point.x, point.y, point.radius + 5 + phase * 16, 0, Math.PI * 2);
          context.stroke();
          context.globalAlpha = 1;
        }
        animation = requestAnimationFrame(draw);
      }
    };
    const resize = () => {
      const bounds = frame.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(bounds.width * ratio));
      canvas.height = Math.max(1, Math.round(bounds.height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (!initialized) {
        cameraRef.current = { ...fitCamera(layout, bounds), rotation: 0 };
        initialized = true;
      }
      draw();
    };
    redrawRef.current = () => {
      cancelAnimationFrame(animation);
      draw();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(frame);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(animation);
    };
  }, [focusedId, hoveredId, layout, reducedMotion, relationLabel, selectedId]);

  useEffect(() => {
    const id = focusedId ?? selectedId,
      node = layout.nodes.find((item) => item.id === id),
      frame = canvasRef.current?.parentElement;
    if (!node || !frame) return;
    const bounds = frame.getBoundingClientRect();
    const zoom = Math.max(cameraRef.current.zoom, focusedId ? 1.3 : 1.05);
    cameraRef.current = {
      ...cameraRef.current,
      zoom,
      x: bounds.width * (selectedId && bounds.width > 900 ? 0.38 : 0.5) - node.x * zoom,
      y: bounds.height * 0.52 - node.y * zoom,
      rotation: 0,
    };
    redrawRef.current();
  }, [focusedId, layout.nodes, selectedId]);

  const keyboard = (event: KeyboardEvent<HTMLButtonElement>, id: string) => {
    const index = nodeIds.indexOf(id);
    if (event.key.startsWith('Arrow')) {
      event.preventDefault();
      const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
      const next = nodeIds[(index + step + nodeIds.length) % nodeIds.length];
      if (!next) return;
      setKeyboardId(next);
      [...document.querySelectorAll<HTMLButtonElement>('[data-brain-node]')]
        .find((el) => el.dataset.brainNode === next)
        ?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect(id);
    }
  };

  return (
    <div className={styles.canvasStage}>
      <canvas
        ref={canvasRef}
        className={styles.canvas}
        data-testid="brain-canvas"
        data-motion={reducedMotion ? 'reduced' : 'full'}
        data-static-neurons="0"
        aria-hidden="true"
        onWheel={(event) => {
          event.preventDefault();
          const b = event.currentTarget.getBoundingClientRect();
          cameraRef.current = zoomCamera(cameraRef.current, Math.exp(-event.deltaY * 0.0012), {
            x: event.clientX - b.left,
            y: event.clientY - b.top,
          });
          redrawRef.current();
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
        }}
        onPointerMove={(event) => {
          const b = event.currentTarget.getBoundingClientRect();
          if (dragRef.current) {
            const dx = event.clientX - dragRef.current.x,
              dy = event.clientY - dragRef.current.y;
            if (Math.abs(dx) + Math.abs(dy) > 2) dragRef.current.moved = true;
            cameraRef.current = panCamera(cameraRef.current, dx, dy);
            dragRef.current.x = event.clientX;
            dragRef.current.y = event.clientY;
            redrawRef.current();
          }
          const id = hit(event.clientX - b.left, event.clientY - b.top);
          onHover({
            node: layout.nodes.find((node) => node.id === id),
            x: event.clientX - b.left,
            y: event.clientY - b.top,
          });
        }}
        onPointerUp={(event) => {
          const b = event.currentTarget.getBoundingClientRect(),
            id = hit(event.clientX - b.left, event.clientY - b.top);
          if (!dragRef.current?.moved && id) onSelect(id);
          dragRef.current = undefined;
        }}
        onPointerLeave={() => onHover({ x: 0, y: 0 })}
        onDoubleClick={(event) => {
          const b = event.currentTarget.getBoundingClientRect(),
            id = hit(event.clientX - b.left, event.clientY - b.top);
          if (id) onFocus(id);
        }}
      />
      <div className={styles.canvasA11yNodes} aria-label={mapNodesLabel}>
        {layout.nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            data-brain-node={node.id}
            tabIndex={node.id === (keyboardId || nodeIds[0]) ? 0 : -1}
            aria-pressed={node.id === selectedId}
            aria-label={`${node.label}, ${typeLabel(node.type)}`}
            onFocus={() => setKeyboardId(node.id)}
            onKeyDown={(event) => keyboard(event, node.id)}
            onClick={() => onSelect(node.id)}
          >
            {node.label}
          </button>
        ))}
      </div>
    </div>
  );
}
