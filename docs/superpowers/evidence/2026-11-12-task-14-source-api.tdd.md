# Task 14 read-only source API — TDD evidence

Source plan: `docs/superpowers/plans/2026-11-12-knowledge-base-phases-0-1.md`, Task 14.

## User journeys

- An authenticated owner can list and page all owned sources, filter by kind and read one source.
- The owner can read a document outline and its latest fragments through one source-shaped API.
- Meeting sources expose transcript segments as timed fragments without changing meeting endpoints.
- Another user cannot distinguish an owned source from a missing source.
- An engine without source adapters returns a stable 501 error on every source route.

## RED and GREEN

| Guarantee | Test target | RED evidence | GREEN evidence |
|---|---|---|---|
| List, pagination and kind filtering | `test_lists_meeting_as_source`, `test_source_list_paginates`, `test_kind_filter` | Routes returned 404 | Passed |
| Owner-only detail, outline and fragments | `test_get_source_of_other_user_is_404`, `test_outline_and_fragments_for_a_document` | Routes returned 404 | Passed |
| Meeting transcript facade | `test_fragments_of_meeting_have_time_locators` | Route returned 404 | Passed |
| Engine availability contract | `test_azure_engine_answers_501` | Four routes returned 404 | Four cases returned 501 with `not_available_on_engine` |
| Authentication and OpenAPI | `test_sources_require_authentication`, `test_openapi_lists_source_paths` | 404 and missing paths | 401 and four documented paths |

Commands executed:

- `.venv/Scripts/python.exe -m pytest tests/test_sources_api.py -q` — RED: 11 checks failed
  because the routes were absent; GREEN: 12 passed including four parameterized engine cases.
- `.venv/Scripts/python.exe -m pytest tests/test_sources_api.py tests/test_api.py -q` — 19 passed.
- `.venv/Scripts/python.exe -m pytest --cov=nodo_api --cov-report=term --cov-fail-under=80 -q`
  — full suite passed; total coverage 85.51%.
- `.venv/Scripts/python.exe -m ruff check src tests` — passed.
- `.venv/Scripts/python.exe -m ruff format --check src tests` — 174 files formatted.
- `.venv/Scripts/python.exe scripts/export_openapi.py` — regenerated
  `packages/contracts/openapi.json` from the application schema.

## Security review

Every route uses the existing authenticated principal dependency. List reads use `list_owned` with
workspace and owner scope. Detail, outline and fragment reads call `get_owned` before reading any
derived data, so absent and other-owner source ids both return the same 404. `kind`, `limit` and
`cursor` are schema-validated. Responses expose only the fields fixed by the Phase 1 contract and
no owner or workspace identifiers.

## Known gaps

The cloud adapters intentionally remain unavailable in Phase 1, so the cloud behavior is the 501
contract rather than a PostgreSQL source implementation. Existing meeting routes are unchanged.
