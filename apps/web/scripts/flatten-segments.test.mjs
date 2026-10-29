import { mkdirSync, mkdtempSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { flatten } from './flatten-segments.mjs';

describe('flatten', () => {
  it('adds the flat name the router requests next to each nested route part', () => {
    const root = mkdtempSync(join(tmpdir(), 'nodo-flat-'));
    mkdirSync(join(root, 'meetings', '__next.meetings'), { recursive: true });
    writeFileSync(join(root, 'meetings', '__next.meetings', '__PAGE__.txt'), 'part');
    mkdirSync(join(root, 'auth', 'callback', '__next.auth', 'callback'), { recursive: true });
    writeFileSync(join(root, 'auth', 'callback', '__next.auth', 'callback', '__PAGE__.txt'), 'deep');
    writeFileSync(join(root, '__next.__PAGE__.txt'), 'root');

    expect(flatten(root)).toBe(2);
    expect(readFileSync(join(root, 'meetings', '__next.meetings.__PAGE__.txt'), 'utf8')).toBe('part');
    expect(readFileSync(join(root, 'auth', 'callback', '__next.auth.callback.__PAGE__.txt'), 'utf8')).toBe(
      'deep',
    );
    expect(existsSync(join(root, '__next.__PAGE__.txt'))).toBe(true);
  });

  it('is safe to run twice', () => {
    const root = mkdtempSync(join(tmpdir(), 'nodo-flat-'));
    mkdirSync(join(root, 'a', '__next.a'), { recursive: true });
    writeFileSync(join(root, 'a', '__next.a', '__PAGE__.txt'), 'x');
    expect(flatten(root)).toBe(1);
    expect(flatten(root)).toBe(0);
  });
});
