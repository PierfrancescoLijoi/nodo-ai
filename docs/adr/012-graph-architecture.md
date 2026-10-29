# ADR-012: Temporal knowledge graph in PostgreSQL, bounded and strictly erasable

Status: accepted (refines ADR-004 and ADR-011)

## Context
The graph answers "what changed", "who owns what" and feeds the Ask screen. It must stay fast as one person's history grows to hundreds of thousands of nodes, must never show a fact without a spoken source, and must forget completely when a meeting is deleted.

## Decision
**Model.** `entities` (person, project, system, topic, decision, action, meeting), `relations` (typed, `proposed | approved | rejected`, confidence, `valid_from/valid_to`) and `relation_evidence` (meeting and segment behind each relation). Entity ids are deterministic (uuid5 of workspace, owner, type and normalized name), so re-processing a meeting never duplicates a node. Relation ids are deterministic from endpoints and kind.

**What the model may say.** The LLM returns entities and relations that point at numbered transcript segments. `validate_extraction` then enforces: known types, allowed endpoint types per relation kind (`ENDPOINTS`), evidence only from real segments of this meeting, hard caps (60 entities, 120 relations, 120-character labels), no mutual SUPERSEDES, and `MENTIONED_IN` is written by the system only. Anything else is dropped, not repaired.

**Writes.** Replace-by-meeting inside one transaction, serialized per owner graph with an advisory lock. Human review (`approved`, `rejected`) survives re-extraction. A rejected relation stays rejected. Supersession is applied only between approved decisions.

**Reads.** Breadth-first search, one SQL query per level. Each frontier node gets a `LATERAL` branch per direction with `ORDER BY confidence DESC LIMIT fanout+1`, so a hub node costs the same as any other and truncation is detectable. Depth is capped at 2, nodes at 200 per request. Only `approved` relations are shown unless the caller asks for proposed ones; `as_of` filters on validity time.

**Erasure.** Deleting a meeting removes its evidence, then sweeps until stable: a relation without evidence goes, an entity without a `MENTIONED_IN` goes, a meeting node without relations goes. Supersessions that the deleted meeting had replaced are restored. No label survives through another meeting's edge.

**Physical design.**
- All three tables are `PARTITION BY HASH (workspace_id)` into 8 partitions; every key starts with `(workspace_id, owner_id)`, so each read is a single partition and a single owner range.
- Covering partial indexes `relations_out_idx` and `relations_in_idx` exclude rejected rows and `INCLUDE` the columns the traversal returns, giving index-only scans in both directions.
- Name lookup: btree prefix on the normalized label plus a trigram GIN for substrings. Recents and change feed have their own partial indexes.
- Row level security is forced on all three tables and keyed on `app.workspace_id`.

## Measured
`services/api/scripts/bench_graph.py`, PostgreSQL 16 in a local container, one owner with 200k entities and 1M relations (skewed degree, a few hubs) next to 40 small workspaces, 1.6 GB with indexes:

| Call | p50 | p95 |
|---|---|---|
| depth 1 from the biggest hub | 8.6 ms | 13 ms |
| depth 2 from the biggest hub | 12.9 ms | 16 ms |
| depth 2 from an ordinary node | 14.9 ms | 18 ms |
| overview (60 recent) | 4.3 ms | 5.4 ms |
| substring name search | 34 ms | 42 ms |

The traversal plan is an index-only scan with zero heap fetches on one partition, and no sequential scan. These are warm-cache, single-client numbers on a laptop, not a capacity claim for Azure.

## Scaling path
1. Now: one Flexible Server, 8 partitions.
2. Read replicas serve `neighborhood`, `find_entities` and digests; writes stay on the primary (they are per-meeting and rare).
3. More partitions or Citus distributes by `workspace_id` without changing queries, since every statement already carries it.
4. A dedicated graph engine is possible behind the same `GraphStore` port, rebuilt from Postgres (ADR-007). Not needed until multi-hop analytics beyond depth 2 are a product requirement.

## Consequences
- Depth beyond 2 is deliberately unavailable; it would need a different cost model.
- Entity merging across spellings is conservative (normalized label equality only); fuzzy merge goes through review, never silently (ADR-004).
- The contract suite in `tests/graph_contract.py` runs against the memory and the PostgreSQL stores, so both must behave the same.
