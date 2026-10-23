# ADR-017: Fail small, degrade honestly, recover in a second region

Status: accepted. The application part is implemented and tested (`resilience.py`, `middleware.py`, `services/query.py`, `jwks.py`). The infrastructure part is in Terraform (`dr.tf`, `apps.tf`, `main.tf`), validated and plan-tested with mocked providers, never applied. No failover has been rehearsed.

## Context
A service that records meetings is used at the worst moments for it to fail (the end of the meeting, the morning after). Three different things can go wrong and each needs a different answer: a dependency is slow or erroring (minutes), a zone is lost (a failure domain inside the region), the region is lost (hours).

## Decision

### 1. Inside the application: a bad dependency must not become a bad service
- **Bulkhead.** Each dependency (database pool, search, models, speech, queue, blob) has a concurrency limit and a bounded wait. When it is full the request is refused fast (`429` with `Retry-After`) instead of queueing until everything is slow.
- **Circuit breaker.** After five consecutive transient failures a dependency is skipped for 20 seconds, then one probe call decides.
- **One retry layer.** Retries live in `resilience.py` with exponential backoff, full jitter and a deadline; SDK retries are switched off so there are never two layers multiplying attempts. Writes the user can repeat are protected by idempotency keys; the paid speech job is never retried at this level.
- **Degrade, don't fail.** If the language model is unavailable, a question returns the retrieved sources and says that the answer could not be generated (`degraded: true`), not an error. Search down means no answer and a `503`, never a made-up one.
- **Shed load at the edge of the process.** A cap on in-flight requests answers `503` with `Retry-After`; health endpoints are never shed. Body size limits per route stop a large request from occupying a worker.
- **Readiness means the database.** `/readyz` checks PostgreSQL only. A replica that cannot reach search can still list meetings and serve the app, so taking it out of rotation for that would turn a partial outage into a total one.
- **Timeouts everywhere**, shorter than the caller's: database connect 5 s, statement 15 s, model 90 s at the gateway, gateway to API 60 s, uvicorn keep-alive above the gateway's idle time.

### 2. Inside the region: three zones, no single instance
- Container Apps environment zone redundant; at least three API replicas (one per zone); rolling revisions with readiness gates.
- PostgreSQL Flexible Server with zone-redundant high availability (synchronous standby in another zone, automatic failover); point-in-time restore for 35 days.
- Service Bus Premium and Search with three replicas (zone-spread by the platform); storage is zone redundant before it is geo redundant.
- The queue is the shock absorber: a slow worker or a throttled model delays results, it does not lose recordings. Messages carry an id, deliveries are bounded (five), then dead-letter and an alert.

### 3. Across regions: warm data, cold compute, one lever
| Layer | Mechanism | Recovery point | Switch |
|---|---|---|---|
| PostgreSQL | cross-region read replica | seconds of replication lag (alert at 60 s) | promote (`dr_promote_database`) |
| Audio, exports, audit | GZRS | minutes (asynchronous geo replication) | account failover |
| Queue | Service Bus geo-DR alias (metadata pairing) | messages in flight are not replicated; the outbox row in PostgreSQL still records each job, and a sweep that re-publishes unfinished ones is not built | failover alias |
| Search index | none: rebuilt from PostgreSQL (ADR-007) | n/a | reindex command (**not built**: it needs a cross-workspace read role) |
| Models, speech | second account in the standby region behind the AI gateway | stateless | automatic (gateway retry) |
| Registry | geo-replication | n/a | none |
| Compute | environment and API in the standby VNet, scaled to zero; worker created only when active | n/a | `dr_active = true` |
| Edge | Front Door origin group, standby origin at priority 2 | n/a | automatic on probe failure |

**Targets** (design targets, to be confirmed by rehearsal): recovery point of seconds for transcripts and graph and minutes for audio; recovery time of 30 to 60 minutes, almost all of it the decision to fail over and the promotion. Front Door sends users to the standby gateway within about a minute of the primary failing its probes, but until the database is promoted the standby serves reads only: the API answers writes with `503` and `Retry-After` (the apps must show that as "try again shortly", which still has to be checked in the client).

The failover is deliberately **not automatic for the database**. Promotion is irreversible and a false positive (a brief regional blip) would split the data. A human decides, following RUNBOOKS.md, with the alert evidence in front of them.

### 4. Ingestion keeps promises it can prove
Jobs are idempotent on `source_hash + pipeline_version + event_id`; the outbox row is written in the same transaction as the meeting state; the retention and export jobs are safe to run twice; the sync import is idempotent. After a failover the outbox is the record of what must be queued again (the sweep that does it is a known gap).

## Alternatives considered
- **Active-active across two regions.** Would remove the manual step but needs a multi-writer database or sharding by tenant. The capacity model (CAPACITY-MODEL.md) shows one write region is enough to 100 000 users; the complexity is not paid for.
- **Backups only (infrastructure as code plus restore).** What version 0.1 offered. Recovery in a day, with a restore time that grows with the data. Rejected for a service that holds meetings.
- **Automatic promotion on a health signal.** See above. Revisit with a rehearsed runbook and a witness (a third place to decide which region is really down).
- **PgBouncer in front of PostgreSQL.** Not needed (30 connections at 100 000 users) and incompatible with the per-transaction settings that row level security uses.

## Consequences
- A failover rehearsal is part of the definition of done for production, once a quarter, on the staging environment: restore from backup into a scratch server, promote the replica of staging, run the smoke test, time every step, record it in RUNBOOKS.md.
- Cost of the standby: a Premium queue, a replica, a gateway unit, a search service sized for a rebuild, idle model and speech accounts, registry replication. Compute is free while scaled to zero.
- Cross-region private connectivity is by design not shared: each region has its own endpoints and DNS. A failover therefore does not depend on anything in the failed region, except Front Door's control plane, which is global.
- Known gaps: no region-level game day has been run; a meeting whose scheduled poll message is lost is not found by any sweep yet (ADR-020).
