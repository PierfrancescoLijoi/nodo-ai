# NODO AI - compliance control map

Status: 23 October 2026. This maps technical controls to the obligations they support and says where the evidence is. It is not legal advice and it is not an audit: nothing here has been assessed by a lawyer, a data protection officer or an auditor, and the Azure side has never run. A control marked *built* exists in code with a test; *configured* exists in Terraform, validated and plan-tested with mocked providers; *open* does not exist yet.

## 1. GDPR

| Obligation | Control | State | Evidence |
|---|---|---|---|
| Lawfulness, information to participants (art. 6, 13-14) | the owner attests to having informed participants before recording; notice version, locale and time stored with the meeting; the app does not claim consent it cannot obtain | built | ADR-005, `recording_attestations`, export contains it |
| Data minimisation (art. 5(1)(c)) | no voiceprints; speaker labels anonymous unless named by the owner; telemetry carries ids only, hashed, no content, no query strings | built | ADR-013, `telemetry.py`, `test_telemetry.py` |
| Storage limitation (art. 5(1)(e)) | audio erased per meeting by policy (7/30/90 days), unknown policy = longest; lifecycle rule at the longest period as backstop; job failure pages | built (job), configured (backstop, alert) | `services/retention.py`, `test_retention.py`, `main.tf` management policy, `monitoring.tf` |
| Erasure (art. 17) | deletion cascades to search, graph and blobs with tombstone; a weekly check finds and removes search documents and graph evidence of deleted meetings that a failed cascade left behind (ADR-021); the answer cache (quotes from transcripts) is dropped for the owner with the same operation and every cached quote is re-checked on each use (ADR-026); audit events keep ids only; stated residual windows: 7 days of blob soft delete, 35 days of database backup | built; windows configured | ADR-007, ADR-019, tests |
| Access and portability (art. 15, 20) | export job produces one JSON with all transcript versions, names, attestation, insights; download through the API; expires in 24 h; audited by id | built | `services/exports.py`, `test_exports.py` |
| Rectification (art. 16) | transcript corrections add versions; speaker names editable; graph relations reviewable | built | ADR-004, ADR-013 |
| Security of processing (art. 32) | encryption in transit and at rest (customer-managed key on audio), private networking, WAF, least-privilege identities, forced row level security, malware scan on uploads, resilience and recovery | built (application), configured (platform) | ADR-016, ADR-017, `rbac.tf`, `network.tf`, `edge.tf` |
| Accountability, records (art. 5(2), 30) | append-only audit log; immutable audit account; activity log; Terraform history | built (log, archive job with a hash chain), configured (immutability) | ADR-019, ADR-022 |
| Data protection by design (art. 25) | tenant isolation in the store, access control inside queries, derived data rebuildable, telemetry redaction by allow-list | built | `SECURITY-BACKLOG.md`, ADR-003 |
| Breach detection and notification (art. 33) | Defender plans, WAF logs, burn-rate and anomaly alerts, runbook for the 72-hour clock | configured; process open | RUNBOOKS.md |
| Processors (art. 28) and transfers (chapter V) | Azure, the model and speech services as processors in EU regions (policy denies other regions; EU data-zone deployments) | configured; paperwork open | `security.tf`; agreements and the data processing addendum to be signed |
| DPIA (art. 35) | recording and analysing conversations at scale is high-risk processing | open | needed before launch |

## 2. EU AI Act (transparency, art. 50, and general obligations)

| Obligation | Control | State |
|---|---|---|
| People know they interact with AI / content is machine-generated | answers carry cited evidence and an answer without evidence is refused (built); that the apps label generated text clearly is to be checked in each UI | built (API), open (UI) |
| No emotion recognition or biometric identification | speaker identity comes only from names the owner types (ADR-013); no voiceprints | built |
| Logging for traceability | request ids and traces end to end; model usage counted per caller at the gateway | configured |
| Risk classification | not a listed high-risk use as designed; confirm if used in employment contexts | open |

## 3. Security-framework controls (ISO 27001 Annex A / SOC 2 common criteria style)

| Area | Control | State |
|---|---|---|
| Access control | Entra only; no shared keys; per-workload identities; no standing human data access; break-glass in Key Vault | configured |
| Cryptography | TLS 1.2+, weak ciphers off at the gateway; CMK RSA-HSM rotated every 90 days; double encryption on audio | configured |
| Operations security | everything as code, plan review, activity log to the workspace, `CanNotDelete` locks, budget alerts | configured |
| Logging and monitoring | diagnostics from every service to one workspace; SLO alerts; availability test | configured |
| Network security | private endpoints, NSG on the gateway subnet, WAF, no public data endpoint except SAS-only audio | configured |
| Resilience | zones, HA database, replica, geo-redundant storage, tested failover | configured; rehearsal open |
| Supplier relationships | Azure and model providers as processors | paperwork open |
| Vulnerability management | container scan in CI (`trivy`), dependency scan, Defender plans, Terraform scan | built (CI), configured (Defender) |
| Business continuity | RPO/RTO targets, runbooks, quarterly rehearsal | targets set; rehearsal open |
| Secure development | tests, type checks, lint, secret scan, independent review of the API (SECURITY-BACKLOG.md) | built |

## 4. What only people can close

1. A data protection impact assessment and the record of processing.
2. Processor agreements with the cloud, model and speech providers; the transfer assessment for any non-EU support access.
3. The retention periods agreed with the business (7/30/90 days) and the audit retention (default 1 year), then locking the audit policy, which cannot be undone.
4. Participant-facing information text for each locale reviewed by counsel (ADR-005).
5. Incident response ownership: who decides, who notifies the authority and the users.
6. An independent penetration test and, if customers require it, an ISO 27001 or SOC 2 audit. These controls make that audit shorter; they do not replace it.
7. A first deployment and a failover rehearsal, so "configured" becomes "operating".
