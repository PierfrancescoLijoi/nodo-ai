# NODO Knowledge Base, Phases 0 and 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write down the revised product (Phase 0) and make the engine source-neutral without changing meeting behavior (Phase 1), so documents can be imported in Phase 2.

**Architecture:** Additive. A `sources` table (kept in step with `meetings` by SQLite triggers), `sections` and `fragments` tables, two new ports (`SourceRepository`, `FragmentRepository`) implemented for memory and SQLite, a graph vocabulary that accepts `SOURCE` nodes and `CHILD_OF` relations, and a read-only `/v1/sources` API. Meeting code paths, PostgreSQL adapters and the Expo contract do not change.

**Tech Stack:** Python 3.13, FastAPI, SQLite (local engine), pytest, ruff. Docs in Markdown.

**Spec:** `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (sections 5, 7, 12, 14, 16). Later phases (2 to 10) are planned when the previous phase closes.

## Global Constraints

- Work on branch `feat/knowledge-base`. **One commit per task**, message = `<type>: <summary>` plus a body that says what changed, why, and how it was checked. Types: feat, fix, refactor, docs, test, chore.
- **Anonymous commits**: no `Co-Authored-By`, no tool or assistant name anywhere in messages or files. Keep commit bodies free of apostrophes and write them with the Write tool to a scratch file, then `git commit -F <file>` (Bash heredocs with apostrophes break).
- **Verify before every commit**, from `services/api`: `.venv/Scripts/python -m ruff check .`, `.venv/Scripts/python -m ruff format --check .`, and the pytest command named in the task. Run ruff only inside `services/api` (from the repo root it uses other settings and reformats files CI does not check). Before the commit of Tasks 10, 11, 12, 13 and 15 run the full `pytest`.
- **Stage explicit paths only** (`git add <paths>`), never `git add -A` or `git add .`.
- Documentation language: English. NODO uses MIT; no GPL/AGPL code or text copied from llm_wiki or VoiceStudio.
- No new Python dependencies. SQLite migrations are forward-only, numbered after `0006`, in `services/api/src/nodo_api/adapters/local/schema/`.
- PostgreSQL adapters, `migrations/*.sql`, Terraform and `apps/mobile` are **not touched**. `/v1/meetings*`, `/v1/query` and sync keep their current behavior and wire format.
- Ids of existing meetings, transcript segments and entities never change. For `kind='meeting'` the source id equals the meeting id and the fragment id equals the segment id.
- CI floors: `pytest --cov=nodo_api --cov-fail-under=80` (pyproject asks 85; do not drop coverage).
- Setup, once: `docs/DESKTOP.md` describes the venv; tests need `services/api/.venv`. PostgreSQL contract tests need Docker Desktop restarted and `docker start nodo-pg-test` (see Task 15).

## Review Focus

The spec implies these failure modes; the task named at the end of each line pins it with a test.

1. A migration that rebuilds `entities`/`relations` must keep relations, evidence and their cascade rules, even when foreign keys are on (Task 10).
2. Deleting or tombstoning a meeting must remove or hide its `sources` row; lists never show tombstoned sources (Tasks 11, 13).
3. Another user must never read someone else's source, outline or fragments; the answer is `NotFoundError`, not a leak (Tasks 12, 14).
4. Invalid locators and fragments: no coordinate set, end before start, page 0, empty text, text over 20,000 characters are rejected at construction (Task 8).
5. On the Azure engine the new endpoints answer 501 `not_available_on_engine`, never a 500 (Task 14).

---

## Phase 0: Foundation (documents only)

Checks for every Phase 0 task: `git diff --check` is empty and the link check passes:

```bash
python tools/local-check/check_md_links.py docs/GLOSSARY.md   # created in Task 1; pass the files of the task
```

### Task 1: Link checker and glossary

**Files:**
- Create: `tools/local-check/check_md_links.py`, `tools/local-check/test_check_md_links.py`, `GLOSSARY.md`

**Interfaces:**
- Produces: `check_md_links.py <file>...` exits 0 when every relative Markdown link and anchor-free path in the given files exists, else prints `file:line: missing target` and exits 1. Function `broken_links(path: Path) -> list[tuple[int, str]]`.

- [ ] **Step 1: Write the failing test** `test_check_md_links.py`: `test_reports_missing_relative_target` (tmp file linking `[x](nope.md)` returns `[(1, "nope.md")]`), `test_ignores_urls_and_anchors` (`https://`, `mailto:`, `#top` are skipped), `test_accepts_existing_target`.
- [ ] **Step 2: Run** `python -m pytest tools/local-check/test_check_md_links.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `broken_links` and the CLI in `check_md_links.py` using `re` over `\]\(([^)]+)\)`; strip any `#fragment` before testing existence.
- [ ] **Step 4: Run** the test again. Expected: 3 passed.
- [ ] **Step 5: Write `GLOSSARY.md`** at the repo root in the domain-modeling format (one context): terms Workspace, Owner, Source, Section, Fragment, Locator, Concept, Collection, Page, Proposal, Evidence, Attestation, Meeting (a Source of kind meeting), each one or two sentences with `_Avoid_` aliases (e.g. Fragment avoids "chunk", "segment" except for transcript phrases; Source avoids "document" as the general word). No implementation detail.
- [ ] **Step 6: Verify** `python tools/local-check/check_md_links.py GLOSSARY.md` exits 0.
- [ ] **Step 7: Commit** `docs: add glossary and a markdown link checker` (body: why a shared vocabulary comes before the model change; checker scope).

### Task 2: ADR-029 source model

**Files:** Create `docs/adr/029-source-model.md`.

- [ ] **Step 1: Write the ADR** (Status: accepted) in the style of ADR-012: Context (every store keyed by `meeting_id`), Decision, Consequences. The Decision must state, as the spec fixes them: `Source` with `kind` in {meeting, document, deck, web, note, audio}; `sources` table filled for meetings by triggers so id equals meeting id; `meetings` stays the meeting detail table; `fragments`/`sections` for non-meeting kinds while meeting transcripts stay in `transcript_segments` behind a facade; additive ports so PostgreSQL adapters are untouched and documents are local-only until Phase 10; **graph nodes: `MEETING` stays for meeting sources and `SOURCE` is added for every other kind** (this is the Phase 1 form of the spec's "alias kept while migrating"; both accepted by `MENTIONED_IN`/`DECIDED_IN`); `Evidence` fields renamed `source_id`/`fragment_id`.
- [ ] **Step 2: Verify** link check on the file, then **commit** `docs: record the source model decision (ADR-029)`.

### Task 3: ADR-030 concept hierarchy

**Files:** Create `docs/adr/030-concept-hierarchy-as-relation.md`.

- [ ] **Step 1: Write the ADR**: `CHILD_OF` (child to parent) as a relation, not a column; several parents allowed; acyclic check on write with recursive query depth cap 6; structural hierarchy (source to section to fragment, collections) auto-approved, inferred concept links are proposals; alternatives rejected (column `parent_id`: single parent, bypasses review and validity time). State that Phase 1 only adds the vocabulary and Phase 3 adds enforcement.
- [ ] **Step 2: Verify and commit** `docs: record concept hierarchy as a relation (ADR-030)`.

### Task 4: ADR-031 pages and human edits

**Files:** Create `docs/adr/031-wiki-pages-and-human-edits.md`.

- [ ] **Step 1: Write the ADR**: pages are derived proposals with per-claim citations validated by the same evidence validator; human revisions are authoritative and stored in `page_revisions`; regeneration produces a new proposal against the human revision; **deletion of a source wins**: claims that cited only erased fragments are removed from the current body and redacted in every stored revision, with a visible marker; superseded fragments make pages `stale`, not deleted. Note this is the one exception to "projections are rebuildable".
- [ ] **Step 2: Verify and commit** `docs: record wiki pages and human edits (ADR-031)`.

### Task 5: Licence policy

**Files:** Create `docs/LICENSING.md`; Modify `NOTICE` (append only if a permissive component is actually used: leave unchanged now).

- [ ] **Step 1: Write `docs/LICENSING.md`**: the rule (MIT repo; reuse only MIT/Apache/BSD code with a `NOTICE` line; GPL/AGPL/non-commercial = ideas only, no code, no copied text; model weights carry their own licence shown in the model catalog), then a table of every project studied with licence and verdict, taken from the survey: Docling MIT reuse, MarkItDown MIT reuse, Unstructured Apache-2.0 second choice, Graphiti Apache-2.0 idea, LightRAG MIT idea, nano-graphrag MIT idea, Microsoft GraphRAG MIT idea, RAPTOR MIT idea, Meetily MIT idea, WhisperLiveKit Apache-2.0 idea, sherpa-onnx Apache-2.0 in use, Jan Apache-2.0 idea, Lemonade Apache-2.0 idea, kokoro-onnx MIT candidate, Handy MIT idea, AnythingLLM MIT idea, llm_wiki GPL-3.0 ideas only, VoiceStudio AGPL-3.0 ideas only, Screenpipe non-commercial ideas only, Khoj/Reor/Basic Memory AGPL-3.0 ideas only, Piper (new) GPL-3.0 excluded, espeak-ng GPL excluded. Mark each licence "read on the project page 2026-11-12" or "from memory, recheck before use".
- [ ] **Step 2: Verify and commit** `docs: add the licensing policy and the survey verdicts`.

### Task 6: Requirements document

**Files:** Create `docs/product/REQUIREMENTS.md`.

- [ ] **Step 1: Write** FR-1 to FR-30 from spec section 6, each with: statement, priority (MoSCoW), phase that delivers it, and 2 to 4 Given/When/Then acceptance criteria that name an observable result (e.g. FR-2: given an imported file, when the same bytes are imported again, then no new version, no new job and the response says `unchanged`). Add the quality-attribute table from spec section 12 with the measured-before-claimed rule, and a traceability table FR to phase to ADR.
- [ ] **Step 2: Verify** every FR id 1..30 appears exactly once: `python -c "import re,sys;t=open('docs/product/REQUIREMENTS.md',encoding='utf-8').read();ids=re.findall(r'^\\| ?FR-(\\d+)',t,re.M);sys.exit(0 if sorted(map(int,ids))==list(range(1,31)) else 1)"`. Expected: exit 0. Then link check.
- [ ] **Step 3: Commit** `docs: add the revised requirements (FR-1 to FR-30)`.

### Task 7: Revised product document

**Files:** Create `docs/product/PRODUCT.md`; Modify `docs/product/blueprint.it.md` (add a first-line notice), `README.md` (status block and link only).

- [ ] **Step 1: Write `PRODUCT.md` v0.2** in English: vision (local personal knowledge base, meetings as one source), personas and a typical day (meeting in the morning, import of a deck, ask across both, review, wiki page), principles (keep README list), screens (spec section 10), what is out of scope, links to spec, requirements and ADRs.
- [ ] **Step 2: Edit** the first lines of `blueprint.it.md`: "Superseded by docs/product/PRODUCT.md (v0.2). Kept as history." Edit README "Current status" with one line pointing to PRODUCT.md and the spec. No other README change.
- [ ] **Step 3: Verify** link check on the three files; **commit** `docs: add the v0.2 product document and mark the blueprint superseded`.

---

## Phase 1: Source-neutral core

Run from `services/api`. Shorthand: `PY=.venv/Scripts/python`.

### Task 8: Source domain types

**Files:**
- Create: `src/nodo_api/domain/sources.py`, `tests/test_sources_domain.py`
- Modify: `src/nodo_api/domain/__init__.py` (export new names)

**Interfaces:**
- Produces (all frozen slotted dataclasses unless noted):
  - `class SourceKind(StrEnum)`: `MEETING="meeting"`, `DOCUMENT="document"`, `DECK="deck"`, `WEB="web"`, `NOTE="note"`, `AUDIO="audio"`.
  - `MAX_FRAGMENT_CHARS = 20_000`.
  - `Locator(time_ms: tuple[int,int]|None=None, page: int|None=None, bbox: tuple[float,float,float,float]|None=None, slide: int|None=None, chars: tuple[int,int]|None=None)`; `__post_init__` raises `ValueError` when none of time_ms/page/slide/chars is set, when a range has end < start or a negative start, when `page`/`slide` < 1, when `bbox` is set without `page`. `to_json(self) -> str` (canonical: sorted keys, no spaces, only set fields) and `Locator.from_json(text: str) -> Locator`.
  - `Fragment(id: str, source_id: str, section_id: str|None, ordinal: int, text: str, locator: Locator, version: int, speaker_id: str|None=None, confidence: float|None=None, locale: Locale|None=None)`; `__post_init__` rejects empty/whitespace text, text longer than `MAX_FRAGMENT_CHARS`, `version < 1`, `ordinal < 0`. `Fragment.from_segment(segment: TranscriptSegment, ordinal: int) -> Fragment` (locator = `time_ms=(start_ms,end_ms)`, ids copied: `source_id=segment.meeting_id`, `id=segment.id`) and `to_segment(self, project: str|None) -> TranscriptSegment` (ValueError when `locator.time_ms` is None or `speaker_id`/`locale`/`confidence` is None).
  - `Section(id: str, source_id: str, parent_id: str|None, ordinal: int, level: int, title: str)`; level >= 1, title non-empty.
  - `Source(id: str, workspace_id: str, owner_id: str, kind: SourceKind, title: str|None, state: str, created_at: datetime, origin: str|None=None, language: Locale|None=None, content_hash: str|None=None, version: int=1, tombstoned_at: datetime|None=None)`; `Source.from_meeting(meeting: Meeting) -> Source` (kind meeting, state = `meeting.state.value`).

- [ ] **Step 0: Baseline.** `$PY -m pytest -q` and `$PY -m ruff check .`. Record the passing count in the commit body. (Planning-time baseline on 2026-11-12: `ruff check` clean, `ruff format --check` 168 files already formatted, pytest exit code 0 with some PostgreSQL and optional-dependency tests skipped.)
- [ ] **Step 1: Write failing tests** in `tests/test_sources_domain.py`: `test_locator_requires_a_coordinate`, `test_locator_rejects_reversed_time_range`, `test_locator_rejects_page_zero`, `test_locator_bbox_needs_page`, `test_locator_json_round_trip_is_canonical` (`Locator(time_ms=(0,5)).to_json() == '{"time_ms":[0,5]}'`), `test_fragment_rejects_empty_text`, `test_fragment_rejects_text_over_limit` (20,001 chars), `test_fragment_from_segment_keeps_ids_and_time`, `test_fragment_to_segment_round_trip`, `test_source_from_meeting_keeps_id_and_state`.
- [ ] **Step 2: Run** `$PY -m pytest tests/test_sources_domain.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** `domain/sources.py` per the interface above; export from `domain/__init__.py`.
- [ ] **Step 4: Run** the file again, then `$PY -m ruff check . && $PY -m ruff format --check .`. Expected: 10 passed, ruff clean.
- [ ] **Step 5: Commit** `feat: add source, section, fragment and locator domain types` (body: ids equal meeting ids for meetings; limits; no behavior change; baseline count).

### Task 9: Evidence speaks of sources and fragments

**Files:**
- Modify: `src/nodo_api/domain/graph.py` (`Evidence`), every use found by `grep -rn "Evidence(" src tests`, `src/nodo_api/adapters/local/graph.py`, `src/nodo_api/adapters/memory/graph.py`, `src/nodo_api/adapters/azure/graph_pg.py` (keyword names only), `src/nodo_api/adapters/local/schema/0007_evidence_source_names.sql` (create), `tests/test_graph_domain.py`, `tests/test_local_migrations.py` (create)

**Interfaces:**
- Consumes: Task 8 nothing (independent).
- Produces: `Evidence(source_id: str, fragment_id: str)` with read-only properties `meeting_id -> source_id` and `segment_id -> fragment_id` kept for old call sites. SQLite columns `relation_evidence.source_id`/`fragment_id` (renamed with `ALTER TABLE ... RENAME COLUMN`, index `evidence_meeting_idx` follows). PostgreSQL columns keep their names; `graph_pg.py` maps names in SQL.

- [ ] **Step 1: Write failing tests**: `test_evidence_has_source_and_fragment_ids_and_legacy_aliases` in `tests/test_graph_domain.py`; in new `tests/test_local_migrations.py` a helper `migrate_to(path, version)` that applies `migration_files()` up to `version` and tests `test_evidence_columns_renamed_and_data_kept` (insert one relation + one evidence row under schema 0006, open `LocalDatabase`, assert the row is readable as `source_id`/`fragment_id`).
- [ ] **Step 2: Run** both; expected FAIL.
- [ ] **Step 3: Implement** the rename in the domain, migration `0007`, and the SQL strings in `adapters/local/graph.py`; keep kwargs/positional usage working; update `graph_pg.py` only where it constructs `Evidence`.
- [ ] **Step 4: Run** `$PY -m pytest tests/test_graph_domain.py tests/test_local_migrations.py tests/test_graph_memory.py tests/test_graph_sqlite.py tests/test_graph_api.py -q` (all pass), then the full `$PY -m pytest -q` (same pass count as baseline plus the new tests; tests that need PostgreSQL stay skipped as before), ruff clean.
- [ ] **Step 5: Commit** `refactor: name graph evidence by source and fragment` (body: legacy aliases, SQLite column rename, PostgreSQL unchanged and why).

### Task 10: Graph vocabulary: SOURCE nodes and CHILD_OF

**Files:**
- Modify: `src/nodo_api/domain/graph.py`, `src/nodo_api/adapters/local/graph.py`, `src/nodo_api/adapters/memory/graph.py`, `src/nodo_api/adapters/local/db.py`
- Create: `src/nodo_api/adapters/local/schema/0008_graph_source_vocabulary.sql`, `tests/source_graph_contract.py`
- Modify: `tests/test_graph_memory.py`, `tests/test_graph_sqlite.py` (mix in the new contract), `tests/test_local_migrations.py`

**Interfaces:**
- Produces: `EntityType.SOURCE = "source"`; `RelationKind.CHILD_OF = "CHILD_OF"`; `source_entity_id(workspace_id: str, owner_id: str, source_id: str) -> str` (same scheme as `meeting_entity_id`, type SOURCE). `ENDPOINTS`: `MENTIONED_IN` and `DECIDED_IN` accept target MEETING or SOURCE; `CHILD_OF: (_THINGS, _THINGS)`. `_ANY` excludes both MEETING and SOURCE. `validate_extraction` drops entities of type SOURCE exactly as it drops MEETING. Class `SourceGraphContract` (only memory and SQLite run it): a SOURCE node dies when nothing targets it, an entity dies when its last non-rejected `MENTIONED_IN` goes, `erase(ws, source_id)` removes evidence and sweeps, exactly like meetings.
- `LocalDatabase` honors a first-line header `-- nodo:foreign-keys-off` in a migration: runs `PRAGMA foreign_keys=OFF` before `BEGIN`, `PRAGMA foreign_key_check` (must return no rows) before `COMMIT`, then `PRAGMA foreign_keys=ON`.

- [ ] **Step 1: Write failing tests**: `test_source_node_lifecycle_matches_meeting_node`, `test_erase_by_source_id_sweeps_orphans`, `test_child_of_requires_things` (task/person rejected, topic-to-topic accepted) in `source_graph_contract.py`; in `test_local_migrations.py`: `test_rebuild_keeps_relations_and_evidence` (rows inserted at schema 0007 survive 0008 with the same ids and the evidence cascade still deletes evidence when a relation is deleted), `test_foreign_key_check_runs_after_rebuild`.
- [ ] **Step 2: Run** them; expected FAIL.
- [ ] **Step 3: Implement** domain changes; the migration rebuilds `entities` (CHECK now includes `'source'`; the partial index `entities_recent_idx` excludes both `'meeting'` and `'source'`) and `relations` (CHECK includes `'CHILD_OF'`) with the SQLite 12-step procedure (create new table, copy, drop old, rename, recreate indexes). Before writing it, run `grep -n "REFERENCES" src/nodo_api/adapters/local/schema/*.sql` and recreate every dependent foreign key (`relation_evidence`, `entity_vectors`). Update the sweep CASE in `_delete_unsupported` so SOURCE behaves like MEETING, in both stores.
- [ ] **Step 4: Run** `$PY -m pytest tests/test_graph_memory.py tests/test_graph_sqlite.py tests/test_local_migrations.py tests/test_graph_domain.py -q`, then full `$PY -m pytest -q`, ruff clean. If Docker is available also run the PostgreSQL graph contract (Task 15, Step 3) to prove the PG store is unaffected.
- [ ] **Step 5: Commit** `feat: allow source nodes and child-of relations in the graph` (body: why SQLite needs a rebuild, the foreign-key handling, PostgreSQL untouched, MEETING nodes untouched).

### Task 11: Sources, sections and fragments in SQLite

**Files:**
- Create: `src/nodo_api/adapters/local/schema/0009_sources.sql`
- Modify: `tests/test_local_migrations.py`

**Interfaces:**
- Produces tables (all `workspace_id`-first keys):
  - `sources(workspace_id, owner_id, id, kind CHECK in the six kinds, title, state, origin, language, content_hash, version INTEGER NOT NULL DEFAULT 1, created_at, tombstoned_at, PRIMARY KEY (workspace_id, id))` with index `(workspace_id, owner_id, created_at DESC, id DESC) WHERE tombstoned_at IS NULL`.
  - `sections(workspace_id, source_id, id, parent_id, ordinal, level, title, PRIMARY KEY (workspace_id, source_id, id), FOREIGN KEY (workspace_id, source_id) REFERENCES sources ON DELETE CASCADE)`.
  - `fragments(workspace_id, source_id, version, id, section_id, ordinal, text, locator, speaker_id, confidence, locale, PRIMARY KEY (workspace_id, source_id, version, id), FOREIGN KEY ... REFERENCES sources ON DELETE CASCADE)`; `CHECK (length(text) BETWEEN 1 AND 20000)`.
  - Triggers `meetings_to_sources_insert`, `_update` (title, state, tombstoned_at, project not copied), `_delete` on `meetings`, and a backfill `INSERT ... SELECT` of existing meetings with `kind='meeting'`. `sources.state` for meetings equals `meetings.state`. `owner_id` comes from `meetings.owner_id`.

- [ ] **Step 1: Write failing tests** in `test_local_migrations.py`: `test_existing_meetings_are_backfilled_as_sources`, `test_new_meeting_appears_as_source_with_same_id_and_state`, `test_meeting_update_and_tombstone_reach_sources`, `test_meeting_delete_removes_source`, `test_deleting_a_source_cascades_to_sections_and_fragments`, `test_fragment_text_limit_enforced`.
- [ ] **Step 2: Run**; expected FAIL.
- [ ] **Step 3: Implement** the migration as specified.
- [ ] **Step 4: Run** `$PY -m pytest tests/test_local_migrations.py tests/test_local_adapters.py -q`, then full pytest, ruff clean.
- [ ] **Step 5: Commit** `feat: add sources, sections and fragments to the local database` (body: triggers keep meetings and sources in step at the database level; backfill; no Python meeting code changed).

### Task 12: Source and fragment ports with memory and SQLite adapters

**Files:**
- Modify: `src/nodo_api/ports/protocols.py`, `src/nodo_api/ports/__init__.py`
- Create: `src/nodo_api/adapters/local/sources.py`, `src/nodo_api/adapters/memory/sources.py`, `tests/source_contract.py`, `tests/test_sources_memory.py`, `tests/test_sources_sqlite.py`
- Modify: `src/nodo_api/adapters/memory/__init__.py`, `src/nodo_api/bootstrap.py`, `src/nodo_api/adapters/local/bootstrap.py` (add `sources` and `fragments` to the container)

**Interfaces:**
- Consumes: Task 8 types; Task 11 tables.
- Produces:
  - `class SourceRepository(Protocol)`: `add(workspace_id: str, source: Source) -> Source` (kinds other than meeting only; meeting raises `ValueError`); `get_owned(workspace_id: str, user_id: str, source_id: str, *, include_tombstoned: bool = False) -> Source` (raises `NotFoundError`); `get_for_processing(workspace_id: str, source_id: str) -> Source`; `save(workspace_id: str, source: Source) -> Source`; `list_owned(workspace_id: str, user_id: str, limit: int, cursor: tuple[datetime, str] | None, kinds: frozenset[SourceKind] | None = None) -> tuple[Source, ...]` newest first, never tombstoned.
  - `class FragmentRepository(Protocol)`: `add_version(workspace_id: str, source_id: str, sections: tuple[Section, ...], fragments: tuple[Fragment, ...]) -> int` (returns the new version number, 1-based, one transaction); `latest(workspace_id: str, source_id: str) -> tuple[Fragment, ...]`; `get_fragment(workspace_id: str, source_id: str, fragment_id: str) -> Fragment | None`; `outline(workspace_id: str, source_id: str) -> tuple[Section, ...]`; `erase(workspace_id: str, source_id: str) -> None`. For a source of kind meeting, `latest` and `get_fragment` read the transcript and return `Fragment.from_segment(...)`; `add_version` and `outline` raise `ValueError` for meetings (meetings are written by the transcript port; outline is empty tuple, not an error).
  - SQLite classes `SqliteSourceRepository(db)` and `SqliteFragmentRepository(db)`; memory classes `MemorySourceRepository`, `MemoryFragmentRepository` (the latter takes the memory transcript repository for the meeting facade).

- [ ] **Step 1: Write failing contract** `tests/source_contract.py` as classes `SourceContract` and `FragmentContract` with fixtures `sources`, `fragments`, `ws`: `test_add_then_get_owned`, `test_other_user_gets_not_found` (Review Focus 3), `test_list_excludes_tombstoned_and_filters_kind`, `test_list_pages_with_cursor`, `test_add_rejects_meeting_kind`, `test_add_version_returns_incrementing_versions`, `test_latest_returns_newest_version_in_order`, `test_get_fragment_by_id_and_missing_is_none`, `test_outline_is_ordered_tree`, `test_erase_removes_fragments_and_sections`, `test_meeting_source_reads_transcript_as_fragments` (create a meeting with two transcript segments through the existing repositories, expect two fragments with `time_ms` locators and identical ids), `test_workspace_isolation`. Wire into `test_sources_memory.py` and `test_sources_sqlite.py` like `test_graph_sqlite.py`.
- [ ] **Step 2: Run** `$PY -m pytest tests/test_sources_memory.py tests/test_sources_sqlite.py -q`; expected FAIL.
- [ ] **Step 3: Implement** ports, both adapters, and container wiring. Local `owned` check is by `owner_id` in the same SQL query that reads the row. Container fields `sources` and `fragments` are `None` on the Azure bootstrap.
- [ ] **Step 4: Run** the two test files, full pytest, ruff clean.
- [ ] **Step 5: Commit** `feat: add source and fragment ports with memory and SQLite adapters` (body: additive ports, meeting facade, Azure container leaves them unset).

### Task 13: Deleting a source

**Files:**
- Create: `src/nodo_api/services/sources.py`, `tests/test_source_service.py`
- Modify: `src/nodo_api/bootstrap.py`, `src/nodo_api/adapters/local/bootstrap.py` (construct the service)

**Interfaces:**
- Consumes: Task 12 ports; existing `GraphStore.erase`, `AnswerCache` invalidation call used in `services/meetings.py:delete`, audit writer used there.
- Produces: `class SourceService` with `__init__(self, sources, fragments, graph, answers, audit, clock)` (match the keyword names `MeetingService` uses for the same collaborators) and `delete(self, workspace_id: str, user_id: str, source_id: str) -> None`: ownership check (`NotFoundError` for another user), tombstone via `save`, `graph.erase`, `fragments.erase`, answer-cache invalidation, audit event `source_deleted` with `{kind}` only. Kind `meeting` raises `ValidationError` ("use the meetings API").

- [ ] **Step 1: Write failing tests** in `tests/test_source_service.py` (memory adapters): `test_delete_removes_fragments_graph_evidence_and_bumps_cache_epoch` (document source with two fragments, a relation citing one, an answer cache epoch; after delete: `latest()` empty, graph `evidence_segment_ids` empty, orphan concept gone, epoch increased), `test_delete_other_users_source_is_not_found`, `test_delete_meeting_source_is_refused`, `test_delete_records_audit_without_title` (Review Focus 2/3).
- [ ] **Step 2: Run**; expected FAIL.
- [ ] **Step 3: Implement** `SourceService.delete` mirroring the order in `MeetingService.delete` (tombstone first so nothing new is read mid-erase).
- [ ] **Step 4: Run** the file, full pytest, ruff clean.
- [ ] **Step 5: Commit** `feat: add the source deletion service` (body: same cascade order as meetings, what is erased, audit content).

### Task 14: Read-only sources API

**Files:**
- Modify: `src/nodo_api/api/v1/routes.py`, `src/nodo_api/api/v1/schemas.py`, `src/nodo_api/api/v1/mapping.py`, `src/nodo_api/errors.py` (if a new error code is needed), `packages/contracts/openapi.json` (regenerate with the script the repo uses: find it with `grep -rn "openapi.json" tools services/api/scripts`)
- Create: `tests/test_sources_api.py`

**Interfaces:**
- Produces (under `/v1`, same auth dependencies as meetings): `GET /sources?kind=&limit=&cursor=` returns `{items: [SourceOut], next_cursor}`; `GET /sources/{id}` returns `SourceOut`; `GET /sources/{id}/outline` returns `{sections: [SectionOut]}`; `GET /sources/{id}/fragments` returns `{version, fragments: [FragmentOut]}`. `SourceOut{id, kind, title, state, created_at, origin, language, version}`, `SectionOut{id, parent_id, ordinal, level, title}`, `FragmentOut{id, section_id, ordinal, text, locator, speaker_id, confidence}` where `locator` is the JSON object of `Locator.to_json()`. When the container has `sources is None` every route answers **501** with error code `not_available_on_engine`. `/v1/meetings*` untouched.

- [ ] **Step 1: Write failing tests** in `tests/test_sources_api.py` (dev auth app from `conftest.py`): `test_lists_meeting_as_source`, `test_get_source_of_other_user_is_404` (Review Focus 3), `test_outline_and_fragments_for_a_document` (insert through the container ports), `test_fragments_of_meeting_have_time_locators`, `test_kind_filter`, `test_azure_engine_answers_501` (app built with `sources=None`; Review Focus 5), `test_openapi_lists_source_paths`.
- [ ] **Step 2: Run**; expected FAIL.
- [ ] **Step 3: Implement** routes, schemas and mapping following the style of the meetings routes; regenerate `openapi.json`.
- [ ] **Step 4: Run** `$PY -m pytest tests/test_sources_api.py tests/test_api.py -q`, full pytest, ruff clean. If a contract test compares `openapi.json` to the app, it must pass.
- [ ] **Step 5: Commit** `feat: add read-only source endpoints` (body: routes, 501 behavior on the Azure engine, meetings unchanged).

### Task 15: Phase 1 documentation and closing gate

**Files:**
- Modify: `docs/API-V1.md`, `docs/ARCHITECTURE.md`, `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (section 7 note: Phase 1 forms of source table, node types, fragment facade)
- Test: full suites

- [ ] **Step 1: Docs.** `API-V1.md`: the four routes and the 501 rule. `ARCHITECTURE.md`: a short "Sources" section naming the ports and the meeting facade. Design doc section 7: a "Phase 1 implementation notes" paragraph listing the four deviations (MEETING and SOURCE nodes coexist; `meetings` is the detail table and `sources` is trigger-filled; Citation and the search index are unchanged until Phases 2 and 4; `CHILD_OF` cycle enforcement arrives in Phase 3).
- [ ] **Step 2: Full local gate.** `$PY -m ruff check . && $PY -m ruff format --check . && $PY -m pytest --cov=nodo_api --cov-fail-under=80 -q`. Expected: all pass, coverage at or above the baseline.
- [ ] **Step 3: PostgreSQL contract run.** Restart Docker Desktop, `docker start nodo-pg-test`, then run the PostgreSQL suites (`tests/azure/test_postgres.py` plus any test the baseline ran against PostgreSQL; the environment variable they need is named at the top of that file). Expected: same pass count as before the phase. If Docker cannot start, say so in the commit body and do not claim PostgreSQL parity.
- [ ] **Step 4: Web and contract checks.** From the repo root `python eval/validate_golden_set.py`; from `apps/web` `npm run typecheck` (the web client is untouched; this proves the OpenAPI change did not break generated types if any).
- [ ] **Step 5: Link check** on the three docs; **commit** `docs: describe the source endpoints and close Phase 1` (body: gate results copied from the runs, what was not verified, e.g. PostgreSQL if skipped).

---

## Self-review notes

- **Spec coverage:** Phase 0 rows of spec section 14 map to Tasks 1 to 7 (the "branch" row is already done: `feat/knowledge-base`). Phase 1 rows map to Tasks 8 to 15: domain (8), evidence naming (9), graph vocabulary (10), migrations and backfill (11, plus 9 and 10), ports and adapters (12), `/v1/sources` (14), delete cascade for non-meeting sources (13), PostgreSQL still green (15). Requirements FR-4, FR-6 (storage only), FR-8 (collections: Phase 3), FR-9 (13) are covered as far as Phase 1 reaches; the rest are in later phases by design.
- **Deliberate narrowing:** Citation, the search index and extraction stay meeting-shaped until Phases 2 and 4 so Phase 1 changes no behavior; recorded in Task 15 and ADR-029.
- **Types:** `Fragment.source_id`/`Evidence.source_id`/`SourceRepository` argument names are the same in Tasks 8, 9, 12, 13, 14.
- **Open for Phase 2 planning:** how `Retriever.index` is generalized to fragments; the parser spike; the import queue job type (`import_source`).
