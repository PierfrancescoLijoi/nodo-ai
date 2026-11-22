import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, isAbsolute, join, normalize, relative, resolve } from 'node:path';

const root = resolve('out');
const port = Number(process.env.PORT ?? 8798);
const types = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.woff2': 'font/woff2',
};

createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(
      new URL(request.url ?? '/', `http://${request.headers.host}`).pathname,
    );
    const relativePath = normalize(pathname).replace(/^[/\\]+/, '');
    let file = join(root, relativePath);
    const fromRoot = relative(root, file);
    if (fromRoot.startsWith('..') || isAbsolute(fromRoot)) throw new Error('Invalid path');
    const info = await stat(file).catch(() => undefined);
    if (info?.isDirectory() || pathname.endsWith('/')) file = join(file, 'index.html');
    const body = await readFile(file);
    response.writeHead(200, {
      'content-type': types[extname(file)] ?? 'application/octet-stream',
      'cache-control': 'no-store',
    });
    response.end(body);
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  }
}).listen(port, '127.0.0.1');
