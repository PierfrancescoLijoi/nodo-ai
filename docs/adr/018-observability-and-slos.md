# ADR-018: Observability starts from what people feel

Status: accepted. Application telemetry is implemented and tested (`telemetry.py`, `test_telemetry.py`). Alerts, diagnostics, the availability test and the workbook are Terraform (`monitoring.tf`), validated and plan-tested with mocked providers, never applied.

## Context
Logs without a question to answer are cost. The questions are: is it up for people, is it fast enough, is the promise being kept this month, what changed, and, when it is not, which dependency. For a product that handles recorded conversations there is a second constraint: telemetry must not become a second copy of the conversations.

## Decision

### Signals
| Signal | Source | Notes |
|---|---|---|
| Traces | OpenTelemetry in the API and worker, exported to Application Insights | W3C `traceparent` from the gateway, through the HTTP call, into the Service Bus message properties and back out in the worker, so a recording is one trace from the upload to the index |
| Metrics | the same SDK plus platform metrics | request rate and latency by route template (HTTP instrumentation); `nodo.stage_seconds`, audio seconds processed, queue age, query latency, degraded answers, model tokens, dependency failures, circuit openings, requests shed, retention deletions and exports built |
| Logs | JSON on stdout, collected by the Container Apps environment | allowed fields only (`event`, `dependency`, `stage`, `status_code`, `duration_ms`, `outcome`, request id); exception messages are dropped, only the exception type is kept |
| Platform diagnostics | diagnostic settings from every data service, the gateway, the edge and the WAF | `allLogs` and `AllMetrics` to one workspace |
| Control plane | activity log with the policy and security categories | who changed what in the subscription |
| Synthetic | a standard availability test from five places, through the public door | `/readyz` with TLS validation |

### What is not collected
Transcript text, audio, prompts, completions and model outputs are never logged or attached to spans. User and workspace identifiers are replaced by a salted keyed hash (`NODO_TELEMETRY_SALT`, in Key Vault): the same person is the same token across logs, and nobody can recover the person without the salt. URL query strings are stripped (they carry SAS tokens). The gateway logs no client address; the WAF keeps its own for the security team. Trace sampling is 25% in the professional tier (head sampling, so errors are sampled like everything else; the gateway always logs its errors).

### Service level objectives
| Objective | Target | Measured as |
|---|---|---|
| Availability of the API | 99.9% over 30 days (43 minutes of budget) | requests without a server error over all requests, excluding health checks and client errors |
| Read latency | p95 under 800 ms | `GET` requests, 15 minute window |
| Time to a transcript | p95 under 15 minutes for a 60 minute recording | end of upload to `review` state (to be measured, speech wait is the unknown) |
| Erasure | 100% of due audio erased within the policy | the retention job reports failures; any failure alerts |

The first two are alerts today. The time-to-transcript objective needs the staged speech pipeline and a metric from real jobs before it can be an alert; it is listed, not claimed.

### Alerts: symptoms page, causes inform
- **Page (severity 0/1):** the availability test failing from two or more places; fast burn (error budget used at 14.4 times the sustainable rate over one hour); Front Door seeing less than half of its origins healthy; audio retention failing.
- **Ticket-level (2/3):** slow burn (6 times over six hours); read latency over objective; PostgreSQL CPU, storage and replica lag; dead-lettered messages; queue backlog; gateway capacity; budget thresholds (actual and forecast).
Every alert has a description that says what the user is feeling, and RUNBOOKS.md has a section for it. Burn-rate alerts use two windows so a short spike does not page and a slow leak does not hide.

### Dashboards
One workbook, "NODO AI overview": requests and errors, latency percentiles, error budget left this month, blocked requests by firewall rule. Anything more is built from a real incident, not in advance.

## Alternatives considered
- **Alert on resource saturation first (CPU, memory).** Cheap and noisy; says nothing about whether people are affected. Kept as the second tier.
- **A third-party observability platform.** Better UX, but it would be a processor of operational data outside the Azure boundary and a new subprocessor to list. Revisit if the team grows.
- **Record prompts for evaluation.** Valuable for quality work, forbidden by design here. Evaluation uses the golden set and opt-in, consented samples in a separate store, not production telemetry.

## Consequences
- The salt is a secret with a rotation consequence: rotating it breaks continuity of the pseudonymous tokens (acceptable; done on suspected exposure).
- Log Analytics retention is 90 days operationally; evidence that must last longer is archived to the immutable audit account (job not built, see ADR-019).
- Alert quality depends on `AppRequests` being populated; the first week in a real subscription is for checking that the queries return rows and tuning thresholds. The queries are skipped at validation (`skip_query_validation`) because the tables do not exist before the first data.
