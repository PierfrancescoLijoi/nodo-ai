# NODO AI

Privacy-first desktop assistant that turns authorised meetings into a temporal, source-backed memory (transcript plus knowledge graph) you can query by text or voice. It runs either on Azure or entirely on your computer with local models, and a local archive can be copied to Azure later.

Status: the engine works end to end on both backends (record, transcribe, separate speakers, extract, graph, ask, erase, copy to the cloud). Azure is untested against a live subscription. How to run the desktop app: [docs/DESKTOP.md](docs/DESKTOP.md). Measurements: [docs/PERFORMANCE.md](docs/PERFORMANCE.md). Product blueprint (Italian, v0.1): [docs/product](docs/product). Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Design system: [docs/DESIGN.md](docs/DESIGN.md). Cloud: [docs/AZURE-ARCHITECTURE.md](docs/AZURE-ARCHITECTURE.md).

UI languages: English, Polish, Italian, Danish.

## Layout

```text
apps/mobile/                Expo (React Native + TypeScript); its web build is the desktop app's interface
services/api/               FastAPI /v1 contract; adapters: memory, Azure, local (SQLite, Whisper, llama.cpp); desktop launcher
services/ingestion-worker/  STT and extraction (later)
services/graphrag/          Retrieval, Cypher, citations (later)
packages/contracts/         OpenAPI shared by API and app
infra/compose/              Local Postgres+pgvector, Neo4j, MinIO
infra/terraform/            Azure (later)
eval/golden_set/            60 provenance, ACL and cross-lingual cases
docs/adr/                   Architecture decision records
tools/speech-bench/         Speech and speaker benchmarks (local and Azure)
tools/local-setup/          Model installer for the local engine
tools/local-check/          End-to-end checks of the local engine with real models
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
