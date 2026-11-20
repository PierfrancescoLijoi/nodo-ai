# Task 13 source deletion — TDD evidence

Source plan: `docs/superpowers/plans/2026-11-12-knowledge-base-phases-0-1.md`, Task 13.

## User journeys

- A source owner can delete a non-meeting source and remove its fragments, graph evidence,
  orphaned graph nodes and cached answers.
- A different user cannot learn whether the source exists and cannot mutate it.
- Meeting deletion stays on the meetings API.
- A deletion audit event records the source kind without recording its title.

## RED and GREEN

| Guarantee | Test | Evidence |
|---|---|---|
| Derived fragments, graph data and cached answers are invalidated | `test_delete_removes_fragments_graph_evidence_and_bumps_cache_epoch` | RED: `ModuleNotFoundError: nodo_api.services.sources`; GREEN: target passed |
| Another owner receives `NotFoundError` and no mutation occurs | `test_delete_other_users_source_is_not_found` | GREEN: target passed |
| Meeting sources are rejected with guidance to use the meetings API | `test_delete_meeting_source_is_refused` | GREEN: target passed |
| Audit metadata is exactly `{kind}` and omits the title | `test_delete_records_audit_without_title` | GREEN: target passed |

Commands executed:

- `.venv/Scripts/python.exe -m pytest tests/test_source_service.py -q` — RED, missing
  `SourceService`; then GREEN, 4 passed.
- `.venv/Scripts/python.exe -m pytest tests/test_source_service.py --cov=nodo_api.services.sources --cov-report=term-missing`
  — 4 passed, 94.12% branch coverage for the new service.
- `.venv/Scripts/python.exe -m pytest -q` — full suite passed (894 collected: 805 passed,
  89 skipped); one pre-existing pytest warning in `test_security.py`.
- `.venv/Scripts/python.exe -m ruff check src tests` — passed.
- `.venv/Scripts/python.exe -m ruff format --check src tests` — 173 files already formatted.

## Security review

Authorization runs before the tombstone or erasure. The repository ownership lookup returns the
same `NotFoundError` for absent and other-owner sources. The tombstone is written before derived
stores are erased so no new read starts mid-erasure. The audit event excludes the source title and
contains no user content. This task adds no dynamic SQL, secrets, network calls or new endpoint.

## Known gaps

The source API is introduced by Task 14, so this task tests the service directly. SQLite behavior
is covered through the Task 12 repository contract; the deletion orchestration uses the same ports
for memory and SQLite.
