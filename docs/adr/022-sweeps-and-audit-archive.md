# ADR-022: A sweep for lost work, and an audit trail nobody can change

Status: accepted. Implemented and tested on memory, SQLite and a real PostgreSQL (including both database roles and their limits); the Azure Blob calls are tested against a fake client only. Terraform: validated and plan-tested, never applied.

## Context
Two gaps were left open on purpose when the resilience design was written (ADR-017, ADR-019).

**Lost work.** A system of queues loses things in ordinary ways. A job is recorded in the outbox and the process dies before it is sent. A message is sent and later vanishes: a purge, a misconfigured dead-letter, a queue failover that does not replicate messages in flight (Service Bus Geo-DR copies metadata only). The visible symptom is a meeting that stays in "processing" for ever and a person who waits.

**Evidence.** The audit trail lives in PostgreSQL, where the application can append to it but never edit it. A person with administrative access to the database could still change it. ADR-019 promised an account with a time-based retention policy; the account existed and nothing wrote to it.

## Decision

### The sweep
A job every 30 minutes, run by the maintenance identity (ADR-021), asks two questions across all workspaces:

1. **Jobs recorded and never sent.** Rows in the outbox with no publication time, older than ten minutes (younger ones are probably being sent right now). Publishing them again is exactly the original operation: the job identity is the key, so a job already sent is not sent twice.
2. **Meetings stuck in processing.** State `transcribing` or `extracting` and a processing job that began more than three hours ago: longer than the speech budget (two hours) plus extraction. They are started again with a new, per-day job key. Processing is idempotent at every stage and the speech stage reads its stored job handle, so "start again" resumes: it polls the existing provider job (which gives up if it is over budget), or submits one if there is none.

The job fails when it could not send something, which raises an alert; a sweep that finds anything at all raises a lower-severity one, because a message was lost somewhere and somebody should know why. The role (`nodo_maintenance`, migration 0008 adds one read policy on the job table) sees identifiers only.

### The audit archive
An hourly job, run by a dedicated identity (`audit`) with its own database role (`nodo_audit`, migration 0009): it reads audit events across workspaces and keeps a one-row cursor table that only it may touch (the application roles are explicitly refused, because moving the cursor would hide events).

- **Files.** JSON lines, in order, in batches of up to a thousand events, into the private audit account whose container has a time-based retention policy: once written, a file can neither be altered nor deleted before the period ends. Writes are create-only.
- **Chain.** The first line of every file is a header that carries the SHA-256 of the previous file. Reading the files in name order and checking each header (`verify_chain`) proves none was removed or changed, even by someone who could write to the account but not break the retention policy.
- **Repeatable.** The name and the content of a batch are determined by where the previous batch ended, so a run that crashed after writing and before moving its cursor does exactly the same thing again and finds the file already there.
- **Late commits.** Events younger than five minutes wait: a transaction that began earlier may still commit with an earlier timestamp. Audit appends are single inserts, so five minutes is generous.
- **Content.** Event type, workspace, event id, the small metadata map (identifiers only, by rule since ADR-019) and the time. No transcript, title or name ever reaches the archive.
- **Silence is an alert.** A job that does not run leaves nothing to alert on, so the alert is on the *absence* of its summary line for three hours.

## Alternatives considered
- **Event-driven sweep (a timer message per meeting).** Precise, but it is one more thing that can be lost. A periodic scan of the state is the thing that cannot be lost.
- **Ship audit events to Log Analytics only.** Searchable and already there, but its retention is operational (90 days) and it is editable by anyone with workspace rights. The archive is for evidence; Log Analytics stays for operations.
- **Stream audit events as they happen (Event Hubs, change feed).** Lower latency than an hour and a new service to run. An hour is fine for evidence that is read in an audit or an incident.
- **Immutability at the storage account level.** Convenient, but a single policy for everything in the account; the container-level policy is enough here and can be locked independently.

## Consequences
- A meeting can still wait up to three hours plus the sweep interval before being restarted. The thresholds are conservative on purpose: restarting a healthy meeting is harmless but wasteful, and the speech stage already has its own two-hour budget.
- The retention policy is created unlocked (`audit_policy_locked = false`). Locking is irreversible; compliance chooses the period first.
- The cursor is a single row: one archive job at a time (the schedule runs one replica and the job keeps `parallelism = 1`).
- Not covered: reading the archive back for an auditor (a notebook or a script over `verify_chain`), and exporting the Log Analytics activity log into the same account.
