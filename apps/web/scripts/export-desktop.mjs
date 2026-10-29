// Puts the built web app where the desktop engine looks for it (docs/DESKTOP.md).
import { cpSync, existsSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const built = fileURLToPath(new URL('../out', import.meta.url));
const target = fileURLToPath(new URL('../../../services/api/src/nodo_api/web', import.meta.url));

if (!existsSync(`${built}/csp.json`)) {
  throw new Error('out/ is missing or not sealed: run "npm run build" first');
}
rmSync(target, { recursive: true, force: true });
cpSync(built, target, { recursive: true });
console.warn(`web app copied to ${target}`);
