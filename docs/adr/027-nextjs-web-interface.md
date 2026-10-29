# ADR-027: A Next.js web interface, one build for the desktop engine and for Azure

Status: accepted (supersedes the use of the Expo web export as the desktop interface in ADR-014; the Expo app stays for Android, ADR-001)

## Context
The interface of the desktop app was the web export of the Expo (React Native) app. The owner wants the interface in React and Next.js, on both backends (this computer and Azure), built to be run in production: scalable, secure, with an identity flow that has been exercised end to end. The same screens have to work against the local engine (a secret in the address fragment, loopback only) and against the cloud API (Entra External ID tokens behind Front Door and API Management).

## Decision
**One static build, two hosts.** `apps/web` is a Next.js App Router application exported as static files (`output: 'export'`). Every page is a client component: the data needs a token, so there is nothing to render on a server, and a static artifact has no server to attack, patch or scale.

| | Local engine | Azure |
|---|---|---|
| Served by | the engine's file server (`SinglePageFiles`) | an nginx container (`apps/web/Dockerfile`), one per region stamp, behind the API gateway |
| How it knows where it is | no `/config.json`; the launcher passes the engine's key in `#token=` | `/config.json`, rendered from the container's environment at start |
| Sign-in | the key | authorization code flow with PKCE against Entra External ID, in the browser (oauth4webapi), access token in memory and session storage |
| API | the page's own origin | the page's own origin: Front Door sends `/v1` to the gateway and everything else to the web API under `/ui` |

The build is made once and promoted by digest like the API image: nothing deployment-specific is compiled in. A deployment that publishes `/config.json` is the cloud and nothing else; an address fragment can not turn it into a local page.

**Content policy from hashes.** Production serves `default-src 'none'`, `script-src 'self'` plus the SHA-256 of each inline script Next emits, `style-src 'self'`, no `unsafe-inline`, no `unsafe-eval`, `frame-ancestors 'none'`. The build computes the hashes (`scripts/seal-csp.mjs`) and fails if a page contains something the policy would block (an inline style or handler). The origins that depend on the deployment (identity provider, audio storage) are filled in when the container starts. The local engine sends its own variant from the same sealed file, which also admits https for the "Copy to Azure" sign-in. Both hosts add `nosniff`, `DENY`, `no-referrer` and a permissions policy that gives the microphone to this origin only.

**Tokens.** The page is a public client. The access token lives in memory and in the tab's session storage with the refresh token; nothing is written to disk and other tabs do not see it. This is the usual trade-off of a single-page application (a script that runs in the page can read its storage); the content policy above is what keeps foreign script out. Concurrent requests share one renewal because a refresh token may be single use; a token refused twice ends the session. A callback is accepted only for a `state` this browser issued, once, and only with the nonce and verifier created for it.

**Routes.** `/`, `/meetings/`, `/meeting/?id=`, `/ask/`, `/graph/`, `/review/`, `/capture/`, `/settings/`, `/auth/callback/`. A meeting is chosen by query string because a static export has no page per id. The router's prefetch asks for route parts under a flat file name (`__next.meetings.__PAGE__.txt`). A build on Linux writes exactly that; a build on Windows (the desktop export) writes nested folders instead, so `scripts/flatten-segments.mjs` adds the flat names and does nothing where they already exist.

**Data.** A typed repository over the `/v1` contract validates every response with Zod at the boundary, TanStack Query caches and polls, reads are retried on 429 and 503 honouring `Retry-After`, writes only with their `Idempotency-Key` (docs/API-V1.md). Audio goes straight to the blob by SAS link, in 4 MiB blocks above 256 MiB. This needs the audio storage account to allow the web origin (CORS, `PUT` only), which the Terraform module now sets.

**Azure hosting.** In the professional tier Front Door has two routes on the same origin group: `/v1/*` and the probe go to the API as before; `/*` carries a rule set that rewrites `/` to `/ui/`, which the gateway serves from a second API (`web`) with no token requirement (nobody has signed in when the page is fetched), the same Front-Door-only check and a per-address rate limit. Hashed build files are cached at the edge; pages and `/config.json` are `no-cache` and `no-store`. In the basic tier the web container has its own public address and the API allows its origin (CORS). A deployment without `web_client_id` has no web interface.

## Alternatives considered
- **Next.js server (SSR, route handlers) with a server-side session (BFF).** Keeps tokens out of the browser, but needs a session store or encrypted cookies that outgrow 4 KB, a confidential client, and a Node runtime to patch and scale, for screens that render nothing without a token. Rejected for this product; revisit if the threat model moves tokens out of the page.
- **Static website on a storage account.** Needs a Front Door private link approval step that Terraform can not complete.
- **Serving the files from the API image.** Fewer pieces, but a UI change would redeploy the API and static traffic would compete with API load shedding.
- **MSAL.** Heavier, and its External ID handling needs per-authority configuration; the standard flow on a certified library is smaller and tested against a stand-in provider here.

## Consequences
- The Expo web export is no longer the desktop interface: `npm run export:desktop` now lives in `apps/web`. The Expo app remains the Android client and keeps its own copy of the API client code until it is retired.
- The Entra app registration for the web app is a single-page application with redirect URIs `https://<host>/auth/callback/` and, for "Copy to Azure" from the desktop app, `http://localhost:8765/auth/callback/` (the engine's preferred port). Its client id must be in `oidc_allowed_client_ids` (a Terraform precondition checks it).
- Verification: unit tests (API client, uploads, mappers, session, PKCE flow, recorder), 20 browser tests against the real local engine with stand-in models and against an OpenID Connect stand-in that verifies PKCE (sign-in, renewal, revocation, sign-out, deep links, recording with a microphone, upload, export, accessibility to WCAG 2.2 AA in both themes), the web image started the way Azure starts it (`tools/verify/web-container-check.sh`) and Terraform plan tests for both tiers.
