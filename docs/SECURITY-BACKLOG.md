# Security backlog

Source: independent security review of `services/api` on 22 October 2026. Findings 1 to 6, 8, 9, 11 to 15, 19 are fixed and covered by `services/api/tests/test_security.py`; the items deferred then are tracked below.

Closed on 23 October 2026 (tests in `test_jwks.py`, `test_edge.py`, `test_telemetry.py`, `test_exports.py`, `test_retention.py`):

| Item | How it is closed |
|---|---|
| JWKS fetched and cached with TTL, refresh on unknown `kid` with a cool-down | `jwks.py`: discovery document must name the same issuer and an https `jwks_uri`; cache with TTL, cool-down against `kid` storms, stale-if-error for 24 h |
| Token `azp` check, JWK `alg` filter | `NODO_OIDC_ALLOWED_CLIENT_IDS` pins the client application; only RSA / RS256 / signing keys are accepted |
| Rate limiting per user | API Management policy per user (ADR-016); Front Door per address; the API sheds load by itself |
| Request body size limit | `EdgeProtection`: per-route limits, 1 MiB default, 413 |
| Export: job status scoped per user, audit event | owner-only export, scoped lookups, `data_export_requested` / `data_export_downloaded` audit events by id |
| Uncaught-exception logging includes the traceback | logs carry the exception type only; the allow-list formatter drops everything else |
| Pure ASGI middleware so 500 responses carry the request id | edge middleware is pure ASGI; the request id is a context variable on every log line |
| Attestation key from Key Vault | the key is a Key Vault secret read by the container platform (rotation still open, below) |

Still open; each must be closed before real users:

| Item | Why it waits | Fix when |
|---|---|---|
| Attestation key id and rotation | a rotated key invalidates the signatures of earlier attestations unless old versions stay readable | key id stored with each attestation, verify against a set of versions |
| Audit events for reads and queries; failed-auth events with reason codes | volume and privacy trade-off to decide (ids only) | before launch |
| Roles beyond owner (an admin who can see a workspace's meetings) | there is no admin product yet | when teams exist |
| Audit archive to the immutable account | the account exists, the job does not | before launch |
| Search re-index command | needs a cross-workspace read role | before the standby region is relied on |
| Penetration test, DPIA, processor agreements | people, not code (COMPLIANCE.md) | before launch |

Design rule that came out of the review: a workspace is a tenant, and inside it a meeting belongs to its owner. Every repository, retriever and graph call takes the user or the set of meeting ids they may see, so access control happens inside the store query.
