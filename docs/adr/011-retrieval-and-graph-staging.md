# ADR-011: Azure AI Search for retrieval, graph in Postgres first

Status: accepted

## Context
The blueprint names Azure AI Search for hybrid retrieval and Neo4j for the graph. Neo4j on Azure is a marketplace or self-hosted component with its own cost, networking and deletion story. PostgreSQL has no Polish text-search configuration, so Polish lexical search there would be weak.

## Decision
- Retrieval: Azure AI Search with one analyzer field per language (`en.microsoft`, `pl.microsoft`, `it.microsoft`, `da.microsoft`) plus a vector field. Free tier in dev, Basic in beta.
- Graph: entities, claims and relations live in PostgreSQL tables and are read by the `GraphStore` port with bounded recursive queries (depth 2, node cap). Neo4j is added behind the same port only if graph queries outgrow Postgres.
- Both are projections of the Postgres source of truth (ADR-007), so either can be dropped and rebuilt.

## Consequences
- One less stateful service to run and to erase from in the beta.
- Search is the one fixed monthly cost; a pgvector-only fallback exists for a zero-cost dev mode but is weaker for Polish.
- If Neo4j is added later, only an adapter and a rebuild job change.
