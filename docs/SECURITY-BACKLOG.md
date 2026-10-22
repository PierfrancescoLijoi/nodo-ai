# Security backlog

Source: independent security review of `services/api` on 22 October 2026. Findings 1 to 6, 8, 9, 11 to 15, 19 are fixed and covered by `services/api/tests/test_security.py`. The items below are known and deliberately deferred; each must be closed before a real deployment.

| Item | Why it waits | Fix when |
|---|---|---|
| JWKS fetched and cached with TTL, refresh on unknown `kid` with a cool-down | The key provider is injected and tests use a local key; no real IdP yet | Entra External ID is wired |
| Token type and `azp` checks, JWK `alg` filter | Depends on the chosen IdP token shape | Entra External ID is wired |
| Attestation key id and rotation, key from Key Vault | No Key Vault yet | Azure infrastructure |
| Export needs an admin role and an audit event; job status scoped per user | No roles or job runner yet | Workers exist |
| Audit events for reads and queries; failed-auth events with reason codes | Audit port is in-memory | Postgres audit adapter |
| Rate limiting per user and per workspace | Belongs at the gateway | Container Apps ingress |
| Request body size limit | Belongs at the gateway | Container Apps ingress |
| Locks and unique constraints for concurrent create | In-memory adapters are single-process; the Postgres adapter uses a unique key `(workspace_id, user_id, key)` in one transaction | Postgres adapter |
| Uncaught-exception logging includes the traceback in uvicorn | Needs a log filter that drops exception messages | Observability work |
| Pure ASGI middleware so 500 responses carry the request id | Cosmetic until real operations | Observability work |

Design rule that came out of the review: a workspace is a tenant, and inside it a meeting belongs to its owner. Every repository, retriever and graph call takes the user or the set of meeting ids they may see, so access control happens inside the store query.
