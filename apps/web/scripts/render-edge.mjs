// Image build step: turns the sealed policy (out/csp.json) into the header file nginx includes. The one
// thing left open is ${NODO_CONNECT_SRC}: the identity provider and storage origins of the deployment,
// filled in when the container starts (docker/15-nodo-runtime.sh).
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CONNECT_PLACEHOLDER } from './csp.mjs';

const HEADERS = (policy) => [
  ['Content-Security-Policy', policy],
  ['X-Content-Type-Options', 'nosniff'],
  ['X-Frame-Options', 'DENY'],
  ['Referrer-Policy', 'no-referrer'],
  ['Permissions-Policy', 'microphone=(self), camera=(), geolocation=(), payment=()'],
  ['Cross-Origin-Resource-Policy', 'same-origin'],
  ['Strict-Transport-Security', 'max-age=31536000; includeSubDomains'],
];

export function render(csp) {
  const policy = csp.policy.cloud.replace(CONNECT_PLACEHOLDER, '${NODO_CONNECT_SRC}');
  if (policy.includes(CONNECT_PLACEHOLDER) || !policy.includes('${NODO_CONNECT_SRC}')) {
    throw new Error('the sealed policy has no connect-src placeholder to fill');
  }
  return HEADERS(policy)
    .map(([name, value]) => `add_header ${name} "${value}" always;`)
    .join('\n')
    .concat('\n');
}

export function build(outDirectory, targetDirectory) {
  const csp = JSON.parse(readFileSync(join(outDirectory, 'csp.json'), 'utf8'));
  mkdirSync(targetDirectory, { recursive: true });
  writeFileSync(join(targetDirectory, 'headers.inc.template'), render(csp));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [out = 'out', target = 'edge'] = process.argv.slice(2);
  build(out, target);
  console.warn(`edge headers written to ${target}/headers.inc.template`);
}
