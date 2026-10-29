// The router prefetches a page's parts from `<route>/__next.<segment>.__PAGE__.txt`. A build on Linux writes
// that name; a build on Windows writes `<route>/__next.<segment>/__PAGE__.txt` instead. Static hosts serve
// files by name, so every nested part also gets the flat name the router asks for (a no-op where it exists).
// Without it each prefetch is a 404 and every navigation falls back to a full page load.
import { copyFileSync, existsSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const SEGMENT_DIRECTORY = /^__next\./;

function* files(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else yield path;
  }
}

export function flatten(root) {
  let created = 0;
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const path = join(directory, entry.name);
      if (SEGMENT_DIRECTORY.test(entry.name)) {
        for (const file of files(path)) {
          const inside = relative(path, file).split(sep).join('.');
          const flat = join(directory, `${entry.name}.${inside}`);
          if (!existsSync(flat)) {
            copyFileSync(file, flat);
            created += 1;
          }
        }
      } else {
        walk(path);
      }
    }
  };
  walk(root);
  return created;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const created = flatten(fileURLToPath(new URL('../out', import.meta.url)));
  console.warn(`route parts flattened: ${created} files`);
}
