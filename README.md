# NODO AI

Privacy-first mobile assistant that turns authorised meetings into a temporal, source-backed memory (transcript plus knowledge graph) you can query by text or voice.

Status: early scaffold. The app runs on sample data (no microphone, no AI calls). Product blueprint (Italian, v0.1): [docs/product](docs/product). Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Design system: [docs/DESIGN.md](docs/DESIGN.md).

UI languages: English, Polish, Italian, Danish.

## Layout

```text
apps/mobile/                Expo (React Native + TypeScript), Android-first
services/api/               FastAPI, OIDC, /v1 contract (hexagonal ports and adapters)
services/ingestion-worker/  STT and extraction (later)
services/graphrag/          Retrieval, Cypher, citations (later)
packages/contracts/         OpenAPI shared by API and app
infra/compose/              Local Postgres+pgvector, Neo4j, MinIO
infra/terraform/            Azure (later)
eval/golden_set/            60 provenance, ACL and cross-lingual cases
docs/adr/                   Architecture decision records
```

## Run the app

```bash
cd apps/mobile
npm ci
npm run web        # browser preview
npm run android    # needs a development build for native capture (ADR-001)
npm run typecheck && npm run lint && npm test
```

## Run the API

```bash
cd services/api
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

Local data stores: `docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env.example up -d`.
