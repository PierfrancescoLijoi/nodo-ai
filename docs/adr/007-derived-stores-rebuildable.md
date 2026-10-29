# ADR-007: Search and graph are rebuildable projections

Status: accepted

## Decision
PostgreSQL and Blob are authoritative. Azure AI Search and Neo4j are derived and can be dropped and rebuilt by replaying events. Every derived record keeps `meeting_id` and `segment_id`.

## Consequences
- Model or schema upgrades are replays.
- Erasure cascades are provable by a test.
- If a managed graph is too costly in beta, run Postgres-only retrieval.
