# ADR-019: Compliance is a property of the system, with evidence

Status: accepted. Retention, export, audit and the telemetry rules are implemented and tested; storage immutability, policy assignments, Defender and locks are Terraform, validated and plan-tested with mocked providers, never applied. This is a technical control mapping, not legal advice; see COMPLIANCE.md for the table and for what only a lawyer or an auditor can close.

## Context
NODO AI records conversations among people, some of whom did not install the app. The law that matters first is the GDPR (and the Italian, Polish and Danish implementations), then the EU AI Act for the transparency of AI systems, then whatever a customer's security questionnaire asks for (ISO 27001 and SOC 2 style controls). ADR-005 already decides that the user attests to having informed participants rather than the app claiming to obtain their consent.

## Decision

### Where data lives and who may touch it
- **Residency.** Every resource is created in the primary or the standby region, both in the EU (defaults: Sweden Central and North Europe). An Azure Policy assignment denies creation anywhere else. Model deployments use the EU data-zone deployment type so processing stays in the EU too.
- **Tenancy.** A workspace is a tenant; inside it a meeting belongs to its owner. Every query carries the user or the set of meeting ids they may see, and PostgreSQL enforces `workspace_id` with forced row level security, so a bug in application code cannot read across tenants.
- **Operators.** No human has standing data access. Database access is Entra-only; the administrator password is in Key Vault for break-glass (Key Vault access is in its diagnostic logs). Each workload has its own identity with the narrowest role (AZURE-ARCHITECTURE.md section 7). The retention job connects as a separate database role that can see which meetings are due and bypasses row level security only for that scan.

### Storage limitation (GDPR art. 5(1)(e), 17)
- **Raw audio** is deleted per meeting by a scheduled job (every six hours) according to the retention policy the user attested to (7, 30 or 90 days; an unknown policy is treated as the longest). Deletion is audited by meeting id. A storage lifecycle rule at the longest period is the backstop, so even a failed job cannot keep audio beyond it; the job failing raises a page.
- **Transcripts and the graph** are kept until the owner deletes them; deletion cascades to search documents, graph rows and blobs, with a tombstone and a reconciliation pass (ADR-007).
- **Exports** (see below) expire after 24 hours in the API and are removed from storage after seven days.
- **Erasure is not instantaneous, and the notice says so.** An erased recording stays in blob soft delete for 7 days (the only copy: versioning and the change feed are off on the audio account precisely so that no version outlives an erasure), and the platform lifecycle removes blobs lazily. PostgreSQL point-in-time restore keeps 35 days, so an erased row can persist in a backup for that long. Both windows are stated to users and are the maximum, not the typical delay.

### Access and portability (art. 15, 20)
`POST /v1/export` queues a job that gathers every meeting the person owns (metadata, speaker names, every transcript version, the attestation with its integrity value removed, the graph insights) into one JSON document with a stable format name. The person downloads it through the API, which checks ownership again. The request and the download are audit events carrying only the export id.

### Integrity and accountability (art. 5(2), 30, 32)
- **Audit log.** Append-only in PostgreSQL (the application role has no update or delete), events carry ids, never content. The professional tier adds an **audit account** with a locked-capable time-based retention policy (WORM, default one year): nobody, including administrators, can alter or delete written evidence before the period ends. The archive job that writes to it is **not built**; the account and its policy are.
- **Encryption.** In transit TLS 1.2+ everywhere, weak ciphers off at the gateway. At rest: platform keys everywhere, and a customer-managed key (RSA-HSM in Key Vault Premium, rotated every 90 days) plus infrastructure (double) encryption for the audio account, the store with the most sensitive raw data. PostgreSQL stays on platform keys: a customer-managed key with geo-redundant backups needs a second key vault in the paired region, a cost not yet justified.
- **Change control.** Everything is Terraform in a repository; pull requests show the plan; the activity log is shipped to the workspace; critical data services carry a `CanNotDelete` lock.
- **Posture.** The Microsoft cloud security benchmark initiative is assigned in audit mode (reports drift, never blocks a deploy); Defender plans for storage (with malware scanning on upload), Key Vault, Resource Manager and PostgreSQL are switched on at the subscription by `enable_defender`.

### Transparency of the AI system (EU AI Act, art. 50)
Speaker labels are anonymous unless the owner names them (ADR-013). Every answer cites segments that exist; an answer without evidence is refused rather than invented. The recording attestation and notice version are stored with each meeting.

## Alternatives considered
- **Consent managed by the app for every participant.** Impossible in practice for a meeting recorded by one person; ADR-005.
- **Hard-delete audit events on erasure.** Audit by id carries no personal content; deleting it would remove the evidence that erasure happened. Kept, and documented.
- **Customer-managed keys everywhere.** More operational risk (a lost key is lost data) than benefit for most customers; offered on the account where it matters and documented as an upgrade for others.
- **Immutable everything.** Would conflict with the right to erasure. Immutability is limited to evidence that holds no personal content.

## Consequences
- The audit retention policy is created unlocked (`audit_policy_locked = false`). Locking is irreversible; compliance chooses the period first.
- A real data protection impact assessment, the record of processing, processor agreements with the model and speech providers and the transfer assessment are legal work that these controls support but do not replace (COMPLIANCE.md lists them).
- Verification of the policies against a live subscription is open (the audit archive job exists: ADR-022; the review inbox exists: ADR-025).
