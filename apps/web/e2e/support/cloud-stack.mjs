// A stand-in for the Azure deployment, for the browser tests of the cloud mode: the web app's files with
// the cloud policy and a /config.json, an OpenID Connect provider that checks PKCE like the real one, and
// a small API that accepts only the access token the provider issued. Everything is on one origin, as it
// is behind Front Door.
import { createHash, randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.env.PORT ?? 8798);
const ORIGIN = `http://127.0.0.1:${PORT}`;
const ROOT = resolve(fileURLToPath(new URL('../../out', import.meta.url)));
const CLIENT_ID = 'e2e-client';
const SCOPE = 'api://nodo/access';
const ACCESS_SECONDS = Number(process.env.ACCESS_SECONDS ?? 3600);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
};

const state = { codes: new Map(), accessTokens: new Set(), refreshTokens: new Set(), sequence: 0, log: [] };

const b64 = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
const s256 = (verifier) => createHash('sha256').update(verifier).digest('base64url');
const json = (response, status, body, headers = {}) => {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers });
  response.end(JSON.stringify(body));
};
const readBody = (request) =>
  new Promise((done) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => done(Buffer.concat(chunks).toString('utf8')));
  });

function issueTokens(nonce) {
  state.sequence += 1;
  const accessToken = `at-${state.sequence}-${randomBytes(6).toString('hex')}`;
  const refreshToken = `rt-${state.sequence}-${randomBytes(6).toString('hex')}`;
  state.accessTokens.add(accessToken);
  state.refreshTokens.add(refreshToken);
  const now = Math.floor(Date.now() / 1000);
  const body = {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'Bearer',
    expires_in: ACCESS_SECONDS,
    scope: `${SCOPE} openid profile offline_access`,
  };
  if (nonce) {
    const claims = { iss: `${ORIGIN}/idp`, aud: CLIENT_ID, sub: 'user-1', iat: now, exp: now + 3600, nonce };
    body.id_token = `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64(claims)}.c2lnbmF0dXJl`;
  }
  return body;
}

// The provider answers a browser page on another origin (the local engine's), as Entra does for a
// single-page-application redirect URI.
function allowCrossOrigin(request, response) {
  const origin = request.headers.origin;
  if (origin && /^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) {
    response.setHeader('access-control-allow-origin', origin);
    response.setHeader('access-control-allow-headers', 'content-type');
    response.setHeader('access-control-allow-methods', 'GET, POST');
  }
}

async function identityProvider(request, response, url) {
  allowCrossOrigin(request, response);
  if (request.method === 'OPTIONS') {
    response.writeHead(204);
    return response.end();
  }
  if (url.pathname === '/idp/.well-known/openid-configuration') {
    return json(response, 200, {
      issuer: `${ORIGIN}/idp`,
      authorization_endpoint: `${ORIGIN}/idp/authorize`,
      token_endpoint: `${ORIGIN}/idp/token`,
      jwks_uri: `${ORIGIN}/idp/keys`,
      response_types_supported: ['code'],
      subject_types_supported: ['public'],
      id_token_signing_alg_values_supported: ['RS256'],
      code_challenge_methods_supported: ['S256'],
    });
  }
  if (url.pathname === '/idp/authorize') {
    const query = url.searchParams;
    const valid =
      query.get('client_id') === CLIENT_ID &&
      query.get('response_type') === 'code' &&
      query.get('code_challenge_method') === 'S256' &&
      query.get('code_challenge') &&
      query.get('state') &&
      query.get('nonce') &&
      (query.get('scope') ?? '').includes(SCOPE) &&
      (query.get('scope') ?? '').includes('openid') &&
      /^http:\/\/127\.0\.0\.1:\d+\/auth\/callback\/$/.test(query.get('redirect_uri') ?? '');
    if (!valid) return json(response, 400, { error: 'invalid_request' });
    const code = randomBytes(8).toString('hex');
    state.codes.set(code, {
      challenge: query.get('code_challenge'),
      redirectUri: query.get('redirect_uri'),
      nonce: query.get('nonce'),
      offline: (query.get('scope') ?? '').includes('offline_access'),
    });
    const back = new URL(query.get('redirect_uri'));
    back.searchParams.set('code', code);
    back.searchParams.set('state', query.get('state'));
    response.writeHead(302, { location: back.toString() });
    return response.end();
  }
  if (url.pathname === '/idp/token' && request.method === 'POST') {
    const form = new URLSearchParams(await readBody(request));
    if (form.get('client_id') !== CLIENT_ID) return json(response, 400, { error: 'invalid_client' });
    if (form.get('grant_type') === 'authorization_code') {
      const record = state.codes.get(form.get('code') ?? '');
      state.codes.delete(form.get('code') ?? ''); // a code works once
      if (!record) return json(response, 400, { error: 'invalid_grant' });
      if (record.redirectUri !== form.get('redirect_uri'))
        return json(response, 400, { error: 'invalid_grant' });
      if (s256(form.get('code_verifier') ?? '') !== record.challenge) {
        return json(response, 400, { error: 'invalid_grant', error_description: 'PKCE verification failed' });
      }
      const tokens = issueTokens(record.nonce);
      if (!record.offline) delete tokens.refresh_token;
      return json(response, 200, tokens);
    }
    if (form.get('grant_type') === 'refresh_token') {
      const token = form.get('refresh_token') ?? '';
      if (!state.refreshTokens.delete(token)) return json(response, 400, { error: 'invalid_grant' });
      return json(response, 200, issueTokens(undefined));
    }
    return json(response, 400, { error: 'unsupported_grant_type' });
  }
  return json(response, 404, { error: 'not_found' });
}

const MEETING = {
  id: 'm-1',
  title: 'Cloud planning',
  state: 'ready',
  created_at: '2026-10-21T09:00:00Z',
  duration_ms: 1_800_000,
  summary: 'The queue replaces the synchronous flow.',
  people: ['Marco'],
  topics: ['Ingestion'],
  decision_count: 1,
  action_count: 0,
};

function api(request, response, url) {
  const header = request.headers.authorization ?? '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  state.log.push({ path: url.pathname, token });
  if (!state.accessTokens.has(token)) {
    return json(response, 401, {
      error: { code: 'unauthorized', message: 'Unauthorized', request_id: 'req-1' },
    });
  }
  if (url.pathname === '/v1/meetings') return json(response, 200, { items: [MEETING], next_cursor: null });
  if (url.pathname === '/v1/graph/changes') return json(response, 200, { items: [] });
  if (url.pathname === '/v1/graph/subgraph' || url.pathname === '/v1/graph/reviews') {
    return json(response, 200, { nodes: [], edges: [], depth: 1, truncated: false });
  }
  return json(response, 404, {
    error: { code: 'not_found', message: 'Resource not found', request_id: 'req-2' },
  });
}

function files(response, url) {
  const csp = JSON.parse(readFileSync(join(ROOT, 'csp.json'), 'utf8')).policy.cloud.replace(
    '__CONNECT_SRC__',
    '',
  );
  let path = normalize(decodeURIComponent(url.pathname));
  if (path.includes('..')) return json(response, 400, {});
  let file = join(ROOT, path);
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) file = join(ROOT, '404.html');
  const status = file.endsWith('404.html') ? 404 : 200;
  response.writeHead(status, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
    'content-security-policy': csp,
    'x-content-type-options': 'nosniff',
  });
  response.end(readFileSync(file));
}

createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', ORIGIN);
  if (url.pathname === '/config.json') {
    return json(response, 200, {
      mode: 'cloud',
      apiBaseUrl: '',
      oidc: { issuer: `${ORIGIN}/idp`, clientId: CLIENT_ID, scope: SCOPE },
    });
  }
  if (url.pathname.startsWith('/idp/')) return identityProvider(request, response, url);
  if (url.pathname.startsWith('/v1/')) return api(request, response, url);
  if (url.pathname === '/__test/expire') {
    state.accessTokens.clear(); // the access token lapses; the refresh token still works
    return json(response, 200, { ok: true });
  }
  if (url.pathname === '/__test/revoke') {
    state.accessTokens.clear();
    state.refreshTokens.clear();
    return json(response, 200, { ok: true });
  }
  if (url.pathname === '/__test/log') return json(response, 200, state.log);
  if (url.pathname === '/healthz') return json(response, 200, { status: 'ok' });
  return files(response, url);
}).listen(PORT, '127.0.0.1');
