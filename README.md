# NODO AI

Privacy-first mobile assistant that turns authorised meetings into a temporal, source-backed memory (transcript + knowledge graph) you can query by text or voice.

Status: early scaffold. Product blueprint (Italian, v0.1) lives in [docs/product](docs/product). Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

UI languages: English, Polish, Italian, Danish.

## Layout

```text
apps/mobile/            Expo (React Native + TypeScript), Android-first
services/api/           FastAPI, OIDC, /v1 contract
services/ingestion-worker/  STT, extraction (later)
services/graphrag/      Retrieval, Cypher, citations (later)
packages/contracts/     OpenAPI + JSON schema shared by API and app
infra/compose/          Local Postgres, Neo4j, MinIO
infra/terraform/        Azure (later)
eval/golden_set/        Provenance / ACL / cross-meeting cases
docs/adr/               Architecture decision records
```
