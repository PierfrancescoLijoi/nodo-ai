# NODO AI API

This package is the FastAPI modular monolith for meeting capture, compliance, retrieval, and grounded answers. It uses a source layout and hexagonal ports so the in-memory adapters can be replaced without changing domain or service code.

## Run

From `services/api`, activate the existing virtual environment and run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn nodo_api.main:create_app --factory --app-dir src
```

Development authentication uses the `NODO_DEV_TOKEN`, `NODO_DEV_USER_ID`, and `NODO_DEV_WORKSPACE_ID` settings. The default token is explicitly development-only. Set `NODO_AUTH_MODE=oidc` for OIDC validation; dev tokens are not accepted in that mode.

## Test and lint

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=nodo_api --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Generate the checked-in API contract with:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe scripts\export_openapi.py
```

## Layout

- `domain/` contains immutable models and business rules with no framework dependencies.
- `ports/` contains workspace-scoped protocols.
- `adapters/memory/` contains deterministic local implementations and fictional seed data.
- `services/` orchestrates meetings and evidence-grounded queries.
- `api/v1/` owns request validation and the versioned HTTP contract.

To replace an adapter, implement the matching protocol in `ports/protocols.py` and inject it when building the application container. Keep `workspace_id` explicit on every operation and preserve the pre-retrieval ACL boundary.

