import { describe, expect, it } from 'vitest';

import { foldText, formatClock, formatDate } from './format';

describe('format helpers', () => {
  it('keeps a calendar day on the same day regardless of zone', () => {
    expect(formatDate('2026-11-26', 'en', 'long')).toContain('26');
    expect(formatDate('2026-11-26', 'en')).toContain('26');
  });

  it('folds case and diacritics for search', () => {
    expect(foldText('Łódź', 'pl')).toBe('lodz');
    expect(foldText('Ørsted', 'da')).toBe('orsted');
  });

  it('formats a clock and clamps negative values', () => {
    expect(formatClock(125.9)).toBe('02:05');
    expect(formatClock(-3)).toBe('00:00');
  });
});
