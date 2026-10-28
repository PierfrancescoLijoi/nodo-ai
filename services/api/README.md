# NODO AI API

This package is the FastAPI modular monolith for meeting capture, compliance, retrieval, and grounded answers. It uses a source layout and hexagonal ports so the in-memory adapters can be replaced without changing domain or service code.

## Run

The service fails closed: with no configuration it refuses to start. For local development set the environment explicitly:

```powershell
$env:NODO_ENVIRONMENT = "dev"; $env:NODO_AUTH_MODE = "dev"; $env:NODO_SEED_DEMO_DATA = "true"
$env:NODO_ATTESTATION_HMAC_KEY = "local-development-key-0123456789"
.\.venv\Scripts\python.exe -m uvicorn nodo_api.main:create_app --factory --app-dir src
```

Dev authentication, demo data and the default HMAC key only work when `NODO_ENVIRONMENT` is `dev` or `test`. Production needs `NODO_AUTH_MODE=oidc` (the default) with `NODO_OIDC_ISSUER`, `NODO_OIDC_AUDIENCE` over https and a random `NODO_ATTESTATION_HMAC_KEY` of at least 32 characters. Access control: a workspace is a tenant and a meeting belongs to its owner. See [docs/SECURITY-BACKLOG.md](../../docs/SECURITY-BACKLOG.md).

## Test and lint

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=nodo_api --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Generate the checked-in API contract with:

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py
```

## Layout

- `domain/` contains immutable models and business rules with no framework dependencies.
- `ports/` contains workspace-scoped protocols.
- `adapters/memory/` contains deterministic local implementations and fictional seed data.
- `services/` orchestrates meetings and evidence-grounded queries.
- `api/v1/` owns request validation and the versioned HTTP contract.

To replace an adapter, implement the matching protocol in `ports/protocols.py` and inject it when building the application container. Keep `workspace_id` explicit on every operation and preserve the pre-retrieval ACL boundary.


## Azure backend

`NODO_BACKEND=azure` swaps the in-memory adapters for the Azure ones (see `docs/AZURE-ARCHITECTURE.md` section 12). The same image runs three ways:

| Role | Command | Settings |
|---|---|---|
| API | `uvicorn nodo_api.main:create_app --factory` (image default) | OIDC issuer and audience required |
| Worker (Container Apps job) | `python -m nodo_api.worker` | no OIDC settings needed |
| Bootstrap (job) | `python -m nodo_api.admin bootstrap` | runs as the PostgreSQL Entra administrator |

Install with `pip install -e ".[dev,azure]"`. Build the image from this folder: `docker build -t nodo-api .`.

Integration tests against PostgreSQL run when `NODO_TEST_POSTGRES_DSN` is set to a superuser DSN of a throwaway database (CI starts a service container):

```powershell
docker run -d --name nodo-pg-test -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:0.8.0-pg16
$env:NODO_TEST_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
.\.venv\Scripts\python.exe -m pytest
```
