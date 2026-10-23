# ADR-016: One public entrance (Front Door), one API gateway (API Management), nothing else exposed

Status: accepted. Implemented in `infra/terraform/modules/nodo/{edge,gateway,network,apps}.tf`; never applied to a subscription.

## Context
Version 0.1 of the Azure design exposed the Container App directly: one public hostname, TLS and rate limiting left to the platform and to the code. That is acceptable for a beta of one. For a service that holds recorded conversations it leaves four gaps. Nobody inspects what arrives before it costs compute (no WAF, no bot filtering, no volumetric protection beyond the platform's default). A regional failure takes the service down until someone redeploys. The models are called straight from the application, so one noisy caller can exhaust the token quota for everyone and a throttled region has no alternative. And every protection (rate limits, token validation, body limits) lives in the application, where a bug in a route can bypass it.

The user asked explicitly for a gateway that everything passes through.

## Decision
**Two layers, each doing what it is best at.**

```text
client -> Front Door Premium (WAF, TLS, global anycast) -> API Management (policies) -> Container Apps (internal)
API / worker -> API Management "AI gateway" -> Azure OpenAI in two regions
```

**Front Door Premium** is the only public name. It terminates TLS (managed certificate on a custom domain), runs the managed rule set and the bot rule set, applies a per-address rate limit, probes the gateway in each region every 30 seconds and routes to the standby region when the primary fails the probe. Nothing is cached: this is an API.

**API Management (Premium, zone redundant, a unit in each region)** is injected into the virtual network in external mode, so it has a public address, but the subnet's network security group admits only the Front Door service tag from the internet. Defence in depth: the policy also checks the `X-Azure-FDID` header against this deployment's Front Door id, so another tenant's Front Door cannot reach it by pointing at the same hostname. The policies are the single place for cross-cutting rules:

| Policy | What it does | Why here |
|---|---|---|
| `validate-jwt` | issuer, audience, signature, expiry against the Entra discovery document | an invalid token never reaches a replica |
| `rate-limit-by-key` | requests per minute per user (`sub`), per address when anonymous | fairness counted at each regional gateway, so the limit roughly doubles while both regions serve |
| backend per region | the gateway of a region calls the API of the same region | failover without hairpinning |
| circuit breaker on each backend | five server errors in a minute stops traffic for 30 seconds (or the backend's `Retry-After`) | a failing region is given room to recover |
| no retry on the public API | the application decides what is safe to repeat (idempotency keys) | a gateway retry of a write is a duplicate |
| `X-Request-Id`, `traceparent` | seeds the trace if the client sent none | one id from the edge to the database |
| `/readyz` operation | deliberately does **not** inherit the API policy: probes carry no token, and the response reveals nothing | Front Door and the availability test must be able to ask |

**The AI gateway** is a second API on the same service. Front Door does not route to it (the route covers `/v1/*` and `/readyz` only); the only sources the gateway subnet admits are Front Door, Azure's management plane and the static NAT addresses of the two container subnets, and the policy rejects any token that is not the API's or the worker's. API Management in external mode has no private address, so the callers reach it over the Microsoft backbone from a fixed address rather than over a private link. The upgrade to a fully private path is internal mode behind Application Gateway (alternatives, below). The API and the worker keep calling Azure OpenAI with the same SDK and their own Entra identity, but the endpoint is the gateway:

- `validate-azure-ad-token` admits only the API's and the worker's client ids;
- `llm-token-limit` gives each identity its own tokens-per-minute budget, so ingestion cannot starve interactive questions or the reverse;
- the gateway signs in to the model accounts with its own managed identity, so the callers need no direct role on them in the professional tier;
- on 429 or a server error it retries up to twice, switching to the account in the second region. Model calls are idempotent, so retrying here is safe.

**Container Apps are internal.** The environment uses an internal load balancer inside the virtual network; the app's ingress also restricts source addresses to the gateway subnet. The platform's default public hostname does not exist for this environment.

**The remaining public surfaces are listed, not implied:** Front Door; the audio blob endpoint (see below); Key Vault and the registry only for the operator addresses in `operator_cidrs`. Everything else (database, queue, search, speech, models, audit archive) has no public endpoint.

**Why the audio account stays public.** Phones and the desktop app upload recordings straight to blob storage with a write-only user-delegation SAS valid for 15 minutes, and the Speech service reads them with a read-only SAS. Routing 60 minute uploads through the API would put the heaviest traffic on the most expensive compute, and the Speech service reads from a public address. The account has no shared key, SAS lifetime is capped by policy, anonymous access is off, Defender for Storage scans uploads for malware, versions and soft delete are on, and the workloads use a private endpoint. It is the one public data endpoint, accepted with those compensating controls.

## Alternatives considered
- **Application Gateway WAF in front of API Management.** Regional, so a second layer for failover (Traffic Manager) and a VNet-resident public IP to manage. Front Door gives the global edge, health-based failover and WAF in one resource.
- **Front Door straight to the Container App (no API Management).** Cheaper and one hop less, but rate limiting per user, token validation before compute, and above all the AI gateway would move into code. The cost of a gateway is paid back by the model-sharing policy alone.
- **API Management in internal mode behind Application Gateway.** The most locked-down shape, and what a larger organization would choose; it needs Application Gateway for the public side, a second WAF to operate and more fixed cost. Revisit when the network team wants it.
- **Keep the API's built-in protections only.** They stay (load shedding, body limits, JWKS verification, per-identity checks) as the last layer, because a gateway is configuration and can be bypassed by an operator mistake.

## Consequences
- Fixed monthly cost rises substantially: Front Door Premium, a Premium gateway unit in each region, and a Premium Service Bus. The basic tier exists so a personal deployment does not carry it.
- Two places to look when a request fails; the gateway writes to the same workspace and the request id is the join key.
- Policy XML is validated by API Management at apply, not by Terraform: a typo surfaces on the first apply. The policies are templates in `infra/terraform/modules/nodo/policies/`, and the first apply to a throwaway subscription is the planned check (AZURE-ARCHITECTURE.md section 14).
- Moving from `waf_mode = Detection` to `Prevention` is an operational step after reading a week of logs; false positives on audio-metadata JSON are the likely ones.
- The gateway needs its own capacity management (the `Capacity` metric alert at 70%).
