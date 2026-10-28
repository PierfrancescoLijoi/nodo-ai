import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { cloudPolicy, CONNECT_PLACEHOLDER, inlineScripts, localPolicy, sha256, violations } from './csp.mjs';
import { seal } from './seal-csp.mjs';

describe('inlineScripts', () => {
  it('returns inline bodies and skips external and empty scripts', () => {
    const html =
      '<script src="/a.js"></script><script>var a=1</script><script id="x"> </script><script>b()</script>';
    expect(inlineScripts(html)).toEqual(['var a=1', 'b()']);
  });
});

describe('violations', () => {
  it('flags what the policy would block', () => {
    expect(violations('<p style="color:red">x</p>')).toContain('inline style attribute');
    expect(violations('<style>p{}</style>')).toContain('inline <style>');
    expect(violations('<button onclick="go()">x</button>')).toContain('inline event handler');
  });

  it('accepts a page of files and hashed scripts only', () => {
    expect(
      violations('<link rel="stylesheet" href="/a.css"/><script src="/a.js"></script><script>x()</script>'),
    ).toEqual([]);
  });
});

describe('policy', () => {
  const hashes = [sha256('a()'), sha256('b()')];

  it('allows only this origin and the hashed scripts', () => {
    const policy = localPolicy(hashes);
    expect(policy).toContain(`script-src 'self' ${hashes.join(' ')}`);
    expect(policy).toContain("default-src 'none'");
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).not.toContain('unsafe-inline');
    expect(policy).not.toContain('unsafe-eval');
  });

  it('leaves the deployment origins to the container', () => {
    expect(cloudPolicy(hashes)).toContain(`connect-src 'self' ${CONNECT_PLACEHOLDER}`);
  });
});

describe('seal', () => {
  const page = (body) => `<html><body>${body}</body></html>`;
  const site = (pages) => {
    const root = mkdtempSync(join(tmpdir(), 'nodo-seal-'));
    for (const [name, html] of Object.entries(pages)) {
      mkdirSync(join(root, name), { recursive: true });
      writeFileSync(join(root, name, 'index.html'), html);
    }
    return root;
  };

  it('writes the union of the pages hashes', () => {
    const root = site({
      a: page('<script>one()</script>'),
      b: page('<script>two()</script><script>one()</script>'),
    });
    expect(seal(root)).toEqual({ pages: 2, hashes: 2 });
    const document = JSON.parse(readFileSync(join(root, 'csp.json'), 'utf8'));
    expect(document.scriptHashes).toEqual([sha256('one()'), sha256('two()')].sort());
    expect(document.policy.local).toContain(sha256('two()'));
  });

  it('refuses a page the policy would break', () => {
    expect(() => seal(site({ a: page('<div style="x:y">z</div>') }))).toThrow(/inline style attribute/);
  });

  it('refuses an empty export', () => {
    expect(() => seal(site({}))).toThrow(/next build/);
  });
});
