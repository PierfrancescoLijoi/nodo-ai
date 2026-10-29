// After `next build`: hash every inline script of the exported pages and write out/csp.json, the file both
// hosts read to send the content security policy. Fails when a page contains something the policy blocks.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { cloudPolicy, inlineScripts, localPolicy, sha256, violations } from './csp.mjs';

function* htmlFiles(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* htmlFiles(path);
    else if (entry.name.endsWith('.html')) yield path;
  }
}

export function seal(directory) {
  const hashes = new Set();
  const problems = [];
  let pages = 0;
  for (const file of htmlFiles(directory)) {
    const html = readFileSync(file, 'utf8');
    pages += 1;
    for (const body of inlineScripts(html)) hashes.add(sha256(body));
    for (const problem of violations(html)) problems.push(`${relative(directory, file)}: ${problem}`);
  }
  if (pages === 0) throw new Error(`No exported pages in ${directory}; run "next build" first`);
  if (problems.length) throw new Error(`The content policy would block:\n${problems.join('\n')}`);
  const scriptHashes = [...hashes].sort();
  const document = {
    scriptHashes,
    policy: { local: localPolicy(scriptHashes), cloud: cloudPolicy(scriptHashes) },
  };
  writeFileSync(join(directory, 'csp.json'), `${JSON.stringify(document, null, 2)}\n`);
  return { pages, hashes: scriptHashes.length };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { pages, hashes } = seal(fileURLToPath(new URL('../out', import.meta.url)));
  console.warn(`content policy sealed: ${hashes} inline script hashes from ${pages} pages`);
}
