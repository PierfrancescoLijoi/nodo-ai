# ADR-021: The search index and the graph are checked against the store, and repaired

Status: accepted. Implemented and tested on the memory, SQLite (local engine) and PostgreSQL backends, including a real PostgreSQL for the role and its limits; the Azure Search count is tested against a fake client only, never against the live service.

## Context
ADR-007 says the search index and the graph projections are derived from the store and can be rebuilt. Until now that was a sentence, not a tool. The index drifts from the store in ordinary ways: a search outage while a recording was indexed, a delete whose cascade stopped half way, a transcript corrected after it was indexed, a restore of the database, a standby region whose index never existed. In each case the symptom is the same and silent: a question does not find a recording that is plainly in the archive, or finds text of one that was deleted. The failover runbook had a step, "re-index everything", with nothing behind it. The same holds on a computer: the local engine keeps its index in the same SQLite file and loses it the same ways.

## Decision
One service, **IndexMaintenanceService**, used by the cloud jobs and by the local app. It walks every meeting in the store (deleted ones included) in keyset pages and compares three things.

| Check | Finding | Repair |
|---|---|---|
| search documents for the meeting against the segments of its latest transcript: none | `missing_from_index` | index the transcript |
| fewer documents than segments | `partial_index` | index the transcript (documents are replaced by id) |
| more documents than segments | `stale_index` | erase the meeting's documents, then index |
| a deleted meeting still has search documents | `leftover_index` | erase them |
| a deleted meeting still has graph evidence | `leftover_graph` | erase it (the graph's own erase, which also restores what a rejected supersession had hidden) |
| a live meeting's graph cites a segment its transcript does not have | `dangling_evidence` | ask for a re-analysis through the normal `reanalyze` job, once per day per meeting; reviewed decisions survive, as always |
| an owner has entities with no vector, or with one of another size (ADR-024) | `entity_vectors_missing` | embed them (once per owner per run) |

**Two modes.** `verify` only reads and exits non-zero when anything differs. `repair` fixes and exits non-zero only when a meeting could not be fixed. A meeting that fails is reported by id and exception type and skipped; the run goes on. Every repair writes an audit event with the meeting id and the finding kind, never text. A meeting that has no transcript yet (still in speech or upload) is skipped, not reported.

**What the graph check does and does not do.** The graph is not rebuilt from the text: it holds human decisions (reviews, names) that the text cannot recreate, and re-extraction costs model calls. Index maintenance removes what should not exist and detects evidence that points at nothing; the repair for the latter is the existing re-analysis, which replaces the meeting's machine contribution and keeps the reviewed part. The graph's own search indexes (trigram on entity names, partitioned relation indexes) are database objects maintained by PostgreSQL; there is nothing to rebuild.

### In the cloud
- A database role, `nodo_maintenance` (migration 0007), the second place that looks across workspaces after `nodo_retention`. It can list meetings with five columns (workspace, id, owner, state, deleted or not) and nothing about their content. Everything else (transcripts, graph evidence, the outbox for the re-analysis request, the audit trail) is read or written through the ordinary per-workspace path, so row level security still binds it to one workspace at a time. A test on a real PostgreSQL proves that it can not read a title, change a meeting, write a transcript, or see a transcript without a workspace.
- A dedicated managed identity (`maintenance`) with the narrowest roles: pull the image, read the Key Vault secrets the job references, write the search index and send to the queue in each region, call the model gateway for embeddings. No storage, no speech.
- Two jobs. **index-verify** runs weekly (read only) and its failure raises an alert (`Search index out of step with the database`). **reindex**, one per region stamp, is started by hand (after an outage, a restore, or in the standby region after a failover; RUNBOOKS.md); its default command is the read-only one, so starting it by mistake changes nothing, and the operator overrides the command with `reindex`.

### On a computer
`POST /v1/local/index {mode}` starts the same run in the background; `GET /v1/local/index` reports `{state, mode, checked, skipped, repaired, findings[], failed[]}`. One run at a time, because it embeds text and the model is shared. `NODO_BACKEND=local python -m nodo_api.admin verify-index|reindex` does it from a terminal. There is no role to separate: it is one person's archive.

## Alternatives considered
- **A reindex that always rebuilds everything.** Simple, and the right tool after a loss, but it costs one embedding per phrase for the whole archive. The comparison makes the routine case (nothing wrong) a count per meeting and the repair proportional to the damage; `reindex` after a loss still rebuilds everything, because everything is missing.
- **Detect drift from the index side (list the documents and look each up).** Finds documents with no meeting at all, which the store-side walk can not. Not needed while a delete tombstones the meeting instead of removing the row (it does), and an index listing at scale is a heavy query; revisit if hard deletes are introduced.
- **Use the retention role.** It would mix two reasons to look across workspaces in one identity, and the retention role may change a meeting's `audio_erased_at` while this one may not.
- **Rebuild the graph from the text.** See above: it would discard reviews.

## Consequences
- The weekly verify is the first signal that the search path is broken without anyone noticing; its thresholds are a count of findings, so a single meeting is enough to alert (tune after the first month).
- `stale_index` repair has a short window in which the meeting has no documents (erase, then index). Questions in that window miss that one recording; acceptable for a repair, and the cost of avoiding it is knowing which document ids are extra, which the index can not tell cheaply.
- The cross-workspace listing is paged by `(workspace id, meeting id)` in the database's collation; the order is only used to resume, never shown.
- The apps do not show these controls yet: the local API is there for a settings screen to call.
