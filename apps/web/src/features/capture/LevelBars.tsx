'use client';

import { useEffect, useRef } from 'react';

import styles from '../features.module.css';

const BARS = 28;
// A fixed, varied silhouette; the live level scales it. Heights are set through the CSS object model,
// which the page's content policy allows (inline style attributes in the markup it would not).
const HEIGHTS = Array.from(
  { length: BARS },
  (_, i) => 10 + Math.round(34 * Math.abs(Math.sin(i * 1.43) * Math.cos(i * 0.21))),
);

/** Decorative: it shows that the microphone is live and carries no audio data. */
export function LevelBars({ active, level }: { active: boolean; level: number }) {
  const bars = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    bars.current.forEach((bar, index) => {
      if (!bar) return;
      bar.style.height = `${HEIGHTS[index]}px`;
      const scale = active ? Math.max(0.15, Math.min(1, level * (0.8 + (index % 5) * 0.12))) : 0.15;
      bar.style.transform = `scaleY(${scale})`;
      bar.style.opacity = active ? '1' : '0.4';
    });
  }, [active, level]);

  return (
    <div className={styles.level} aria-hidden>
      {HEIGHTS.map((_, index) => (
        <span
          key={index}
          ref={(element) => {
            bars.current[index] = element;
          }}
          className={styles.bar}
        />
      ))}
    </div>
  );
}
