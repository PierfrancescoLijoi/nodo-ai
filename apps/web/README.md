# NODO web interface

Next.js (App Router, React 19, TypeScript strict), exported as static files. One build runs in two places: the local engine serves it on the desktop, and an nginx container serves it on Azure. Decision and trade-offs: [ADR-027](../../docs/adr/027-nextjs-web-interface.md).

```text
src/app/          routes (thin): /, /meetings, /meeting?id=, /ask, /graph, /review, /capture, /settings, /auth/callback
src/features/     one folder per screen: home, meetings, ask, graph, review, capture, settings, auth
src/components/   shell (navigation, sign-in gate) and ui (primitives, tested)
src/providers/    preferences and i18n, connection (local or Azure), auth, data (repository and query cache)
src/lib/api/      typed client, Zod schemas, mappers, repository, upload, local-engine routes
src/lib/auth/     authorization code flow with PKCE (oauth4webapi), session with shared renewal
src/lib/runtime/  /config.json, connection resolution, safe storage
src/lib/i18n/     English, Polish, Italian, Danish (a test keeps them in step)
scripts/          content policy sealing, route part flattening, nginx header rendering, desktop export
docker/, nginx/   the Azure image
e2e/              browser tests and their stand-in back ends
```

## Develop

```bash
npm ci
# an engine to talk to: the real one, or the one with stand-in models used by the tests
(cd ../../services/api && .venv/Scripts/python scripts/e2e_engine.py --port 8765)
npm run dev        # http://localhost:3000/#token=e2e-local-engine-key-0123456789-0123456789
```

`next dev` forwards `/v1` to `NODO_DEV_API` (default `http://127.0.0.1:8765`). To try the cloud mode, serve `out/` with a `/config.json` (see `e2e/support/cloud-stack.mjs`, which also provides an identity provider and an API).

## Check

```bash
npm run typecheck && npm run lint && npm run format:check
npm test                         # unit tests (npx vitest run --coverage: 80 % of logic and primitives)
npm run build                    # static export in out/, then flattening and the sealed content policy
npx playwright install chromium  # once
npm run test:e2e                 # local engine + Azure stand-in; NODO_PYTHON picks the Python of services/api
```

Set `PW_CHANNEL=msedge` to use Edge instead of the downloaded Chromium.

## Desktop

```bash
npm run export:desktop           # build, then copy out/ to services/api/src/nodo_api/web
```

The engine serves those files with the sealed policy and its own headers (`SinglePageFiles`).

## Azure

```bash
docker build -t nodo-web apps/web
bash tools/verify/web-container-check.sh nodo-web
```

Environment of the container: `NODO_OIDC_ISSUER`, `NODO_OIDC_CLIENT_ID`, `NODO_OIDC_SCOPE` (required, https), `NODO_API_BASE_URL` (only when the API is on another address), `NODO_BLOB_ORIGIN` (the audio storage origin, for the content policy). A wrong value stops the container at start with the reason. The Terraform module sets all of them; see [FIRST-DEPLOY.md](../../docs/FIRST-DEPLOY.md) for the Entra registration.

## Rules that the tests keep

- No inline style, inline event handler or unhashed inline script in any page (the build fails otherwise).
- Every screen has no automated accessibility violations (axe, WCAG 2.2 AA rules) in both themes, works from the keyboard and without sideways scrolling at 375 px.
- No console error from the content policy during any flow.
- User-facing text only through `src/lib/i18n`; no em or en dashes.
