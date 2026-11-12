# ADR-029: Source model, so the archive holds more than meetings

Status: accepted (extends ADR-012; the PostgreSQL schema of ADR-012 is untouched)

## Context
Every store today is keyed by `meeting_id`: transcript segments, search index rows, graph evidence, the answer cache, sync and erasure. That was right while a meeting was the only thing NODO learned from. The knowledge base adds documents, decks, pasted web text, notes and audio files, and each of them needs the same guarantees: a citation that points at a real quote, graph facts that vanish when their source is deleted, and one place to list and review what the archive knows.

Two things must not break. The frozen Expo app and the existing PostgreSQL/Azure adapters rely on meeting ids, segment ids and the `/v1/meetings*`, `/v1/query` and sync wire formats. And the shared contract suites run against the memory, SQLite and PostgreSQL stores, so CI has to stay green while the model grows.

## Decision
**Source.** One `Source` is one thing put into the knowledge base. Its `kind` is one of `meeting`, `document`, `deck`, `web`, `note`, `audio`. A source carries title, state, origin, language, content hash, version, retention and tombstone fields, always scoped by workspace and owner.

**Meetings keep their ids.** The `sources` table is filled for existing and new meetings by triggers on `meetings`, so for `kind = meeting` the source id equals the meeting id. No id is migrated. `meetings` stays the meeting detail table (attestation, speaker names, audio erasure, duration); it is not renamed or folded into `sources`.

**Structure for the other kinds.** Documents, decks, web text, notes and audio files are stored as `sections` (titled, nesting parts of a source) and `fragments` (the smallest citable piece of text, with a JSON locator: time range, page, slide or character span). Meeting transcripts stay in `transcript_segments` and are exposed as fragments through a facade, so fragment id equals segment id for meetings and no row is copied. The evidence rule is the same for every kind: the quoted text must be a substring of the stored fragment.

**Additive ports.** Ports gain new methods and types; the meeting-keyed ones stay. The PostgreSQL adapters are therefore untouched and keep passing the shared contract suites. Documents, hierarchy and pages are local-only until Phase 10: the Azure engine reports them as unavailable. New contract cases carry a marker and run on the memory and SQLite stores first.

**Graph nodes.** `MEETING` stays the node type for meeting sources, and `SOURCE` is added for every other kind. This is the Phase 1 form of the spec rule that an alias is kept while migrating. `MENTIONED_IN` and `DECIDED_IN` accept both types as their target, so the existing sweep and erase rules (an entity without a `MENTIONED_IN` goes) apply to every kind without a new code path.

**Evidence.** The `Evidence` fields are renamed `source_id` and `fragment_id`. For meetings the values are the old `meeting_id` and `segment_id`, unchanged. The PostgreSQL column names in `relation_evidence` stay as they are; only the Python names move.

## Consequences
- No data migration of ids and no change to the wire format: mobile, sync and `/v1/query` behave as before. Document citations travel in new fields that clients opt into.
- Two node types for a source (`MEETING`, `SOURCE`) exist for a while. Code that lists "sources in the graph" must handle both until a later phase merges them; the cost is one extra type check, not a second model.
- The facade over `transcript_segments` is a seam to maintain: a change to segment storage must keep the fragment view identical.
- A trigger-maintained `sources` row means a meeting written by any path appears as a source; tests must cover insert, update and delete of meetings.
- Cloud parity is deferred, not dropped. Phase 10 adds the PostgreSQL side behind the same ports; until then a workspace on Azure sees meetings only.
- Renaming the Python `Evidence` fields touches every reader of the old names in the graph code; the memory, SQLite and PostgreSQL stores must be changed together and checked by the same contract suite.
