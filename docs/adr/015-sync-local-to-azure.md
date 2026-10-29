# ADR-015: Connecting Azure copies the local archive to the cloud

Status: accepted

## Context
A user may work locally first and connect Azure later. The cloud must then hold what exists locally, without re-doing the work (re-transcribing would change the words the person has already reviewed) and without losing human decisions (speaker names, corrected transcripts, approved or rejected graph relations).

## Decision
**Direction and trigger.** One way, on demand: local to cloud. The owner signs in to Azure in the app (Entra, PKCE) and starts a sync. The access token is handed to the local engine for that run and is not stored. The engine sends each meeting with `PUT /v1/sync/meetings/{id}` over HTTPS, oldest first, and keeps a content hash per meeting and cloud address, so the next run sends only what changed. A meeting deleted locally is deleted in the cloud on the next run.

**What travels.** A package per meeting (`SyncPackage`): title, summary, dates, state, speaker names; every transcript version (immutable, so a correction adds a version); the graph contribution as entities and relations identified by type and label, each relation with its review status; the original attestation fields. Audio is not copied in this version.

**What does not travel, and why.** Graph ids differ between workspaces (they are derived from the workspace), so they are not sent: the cloud derives its own from labels, which makes a re-send idempotent. Search embeddings are not sent: the cloud embeds with its own model. The attestation signature is made with a key only the issuing deployment holds, so the cloud signs a new attestation, keeps the original acknowledgement time, and records the original event id and a hash of the original signature in its audit log.

**Idempotence and conflicts.** Sending the same package twice changes nothing. A newer package adds only the difference (new transcript versions, current names and reviews). A meeting id owned by another user is refused (409). A meeting the owner deleted in the cloud is not brought back (409). Edits made in the cloud to a synced meeting can be overwritten by a later local change: the local archive is the source in this version.

**Limits enforced by the receiver.** Package size and counts are capped by the schema; names pass the same checks as speaker naming; segment text and ids are validated; evidence must refer to real segments.

## Consequences
- A bidirectional sync would need per-field timestamps or an operation log; not built.
- Audio copy, resumable upload of large archives and sync of the Azure copy back to a new computer are the natural next steps.
- Switching the app between Azure and local does not move data by itself; syncing does.
