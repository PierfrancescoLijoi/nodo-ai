import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { cloudPolicy, sha256 } from './csp.mjs';
import { build, render } from './render-edge.mjs';

const csp = { policy: { cloud: cloudPolicy([sha256('boot()')]) } };

describe('render', () => {
  it('writes one add_header per security header, always, with the deployment origins left as a variable', () => {
    const text = render(csp);
    expect(text).toContain("add_header Content-Security-Policy \"default-src 'none'");
    expect(text).toContain("connect-src 'self' ${NODO_CONNECT_SRC};");
    expect(text).toContain('add_header X-Frame-Options "DENY" always;');
    expect(text).toContain('add_header Strict-Transport-Security');
    expect(
      text
        .split('\n')
        .filter(Boolean)
        .every((line) => line.endsWith('always;')),
    ).toBe(true);
  });

  it('keeps the script hashes the build sealed', () => {
    expect(render(csp)).toContain(sha256('boot()'));
  });

  it('refuses a policy that has nothing to fill in', () => {
    expect(() => render({ policy: { cloud: "default-src 'none'" } })).toThrow(/placeholder/);
  });
});

describe('build', () => {
  it('reads out/csp.json and writes the template', () => {
    const root = mkdtempSync(join(tmpdir(), 'nodo-edge-'));
    mkdirSync(join(root, 'out'));
    writeFileSync(join(root, 'out', 'csp.json'), JSON.stringify(csp));
    build(join(root, 'out'), join(root, 'edge'));
    expect(readFileSync(join(root, 'edge', 'headers.inc.template'), 'utf8')).toContain(
      'Content-Security-Policy',
    );
  });
});
