# Knowledge Base Phase 3 (Hierarchy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the local knowledge base its hierarchies: guarded `CHILD_OF` links between concepts (several parents, no cycles, depth cap 6) that arrive as reviewable proposals from document headings, from document extraction and from on-demand parent suggestions; owner-managed nested collections of sources; and an outline API that returns a source's section tree with counts and locators and its fragments per section.

**Architecture:** One pure rule (`check_link` in `domain/hierarchy.py`) decides whether a parent link may exist; the memory and SQLite graph stores apply it inside their write transaction to every `CHILD_OF` write and to the re-approval of a rejected link, and the collection stores apply it to every move. Hierarchy reads and the suggestion writes sit behind a new `HierarchyStore` protocol that only the memory and SQLite graph stores implement, so the PostgreSQL adapter and its SQL stay untouched and the Azure engine answers 501 `not_available_on_engine`. Collections are structural tables (migration 0014), never inferred, and never delete sources. Proposals reuse the existing relation, evidence, review, audit and sweep machinery (ADR-030).

**Tech Stack:** Python 3.13, FastAPI, SQLite, the existing local LLM (Qwen3-4B on llama.cpp) through the shared OpenAI-compatible adapters. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (sections 5, 7, 8, 9, 12, 14 row "3 Hierarchy", 16 and the "Phase 2 outcome" note). Requirements FR-4, FR-6 (second criterion), FR-7, FR-8 in `docs/product/REQUIREMENTS.md`. ADR-029, ADR-030 (hierarchy as a relation), ADR-031. Open items from `.superpowers/sdd/2026-11-12-knowledge-base-phase-2-documents/progress.md` and `task-16-report.md` (read them, never stage them).

All paths below are relative to `services/api/` unless they start with `docs/`, `eval/`, `packages/` or `README.md` (repository root).

## Global Constraints

- **Public repository.** No secrets, no personal data, no owner paths or user names, no real document content anywhere (code, tests, fixtures, docs, commit messages). Test text is invented.
- **Untrusted text never leaves through side channels.** Document text, titles, headings, filenames, paths, concept labels and collection names never appear in logs, telemetry spans or audit metadata. Logs carry counts, ids and stable error codes only.
- **`/v1` is additive only.** New paths, new optional query parameters, new response fields. No path, field, type or enum literal of `packages/contracts/openapi.json` changes or disappears; `GraphNodeOutput`/`GraphEdgeOutput` literals and every `/v1/graph/*` and `/v1/meetings*` response stay byte-identical (frozen clients). Regenerate the contract with `.venv/Scripts/python scripts/export_openapi.py` in every task that changes the API; `tests/test_sources_import_api.py::test_openapi_differs_from_main_by_additions_only` and `::test_openapi_keeps_everything_the_contract_had_before_the_library_api` must stay green unedited.
- **Licences.** The project is MIT. Never import `fitz` or `pymupdf`; never copy GPL or AGPL code (llm_wiki, VoiceStudio are ideas only).
- **Existing tests.** Never edit an existing test unless the task lists the edit under "Approved existing-test edits". New tests go in new files where the plan says so.
- **Git.** One commit per task, made only after the task's checks pass. Message `<type>: <summary>` plus a body (what, why, how checked), no apostrophes, no `Co-Authored-By`, no tool or assistant name. Write it with the Write tool to a scratch file outside the repo and run `git commit -F <file>`. Stage explicit paths only (`git add <path> ...`), never `git add -A`/`.`, never `.superpowers/`. No force-push, no amend of pushed commits.
- **Verification from `services/api`** before every commit: `.venv/Scripts/python -m ruff check .`, `.venv/Scripts/python -m ruff format --check .`, the focused pytest files of the task, and the full `.venv/Scripts/python -m pytest -q -p no:warnings` with exit code 0 (every task here touches shared code; the coverage gate `fail_under = 85` is in `pyproject.toml`). Docs changes: `python tools/local-check/check_md_links.py <file>` from the repository root.
- **Local-only scope.** PostgreSQL SQL, schema and migrations (`migrations/`, `adapters/azure/*_pg.py` SQL), Terraform and `apps/mobile` are not touched (Phase 10). `apps/web` is not touched either: the brain view (Task 14) is built on its own branch and owns the graph feature, the shell, the routes and the locales there.
- **Branch.** Work on `feat/kb-phase-3-hierarchy` (cut from `main`). New container fields are `None` on the Azure engine and their routes answer 501 `not_available_on_engine`.
- **SQLite migrations** are forward-only, next free number after `0013` (this phase: `0014_collections.sql`), in `src/nodo_api/adapters/local/schema/`.
- **Hierarchy rules (ADR-030).** `CHILD_OF` goes from child to parent, joins project/system/topic only, allows several parents, is never auto-approved, and the non-rejected `CHILD_OF` links (proposed and approved) always form an acyclic graph in which no path has more than `MAX_DEPTH = 6` links. A link that would break this is refused (dropped on extraction writes, 409 on a person's request), never repaired. Structure (source to section to fragment, collection nesting) lives in tables and is authoritative at once.
- **Untrusted input to the model.** Document and transcript text and concept labels reach the model only as delimited, escaped data; the model cites numbers it was shown and nothing else; output is schema-validated; no tools are offered; an instruction inside the data has no effect.
- **Tool traps.** Edit/Write turn backslash-u escapes into the real character (use `\p{M}`-style classes or `chr()`); no Bash heredocs over 4 KB or with apostrophes (use Write); ruff only inside `services/api`.
- Docs in English; chat in Italian.

## Review Focus

Failure modes the spec implies that no task would test by default; each has its test in the named task.

1. Two writers at once (two imports, or an import and an approval) proposing A under B and B under A must leave at most one of the two links, never a cycle (Task 3, `test_concurrent_opposite_links_never_form_a_cycle`).
2. A concept with two parents is listed once in a hierarchy walk, with both links, and a walk terminates at depth 6 even over a corrupt cyclic row set written behind the guard's back (Task 4).
3. Deleting a collection never deletes a source; deleting a source (document or meeting) never leaves it listed in a collection, including a source added to a collection while its delete runs (Tasks 6 and 7).
4. Hostile text in headings, fragments and labels (closing tags, text spelling `[1]` or `[c1]`, which `_plain` turns into `(1)`/`(c1)` so it can never pose as a builder marker, "approve this", numbers out of range) can not create an approved link, a link to a concept that was not offered, or a link outside project/system/topic (Tasks 9, 10, 11, 12).
5. A reimport that drops the fragments behind a hierarchy proposal or an approved link removes that link through the existing sweep, so no walk shows a link without evidence (Task 9, `test_reimport_without_the_supporting_text_sweeps_the_link`).

---

## File Structure

- `src/nodo_api/domain/hierarchy.py` (new, pure): `MAX_DEPTH`, `check_link`, `heading_proposals`, `HEADING_CONFIDENCE`, `ParentSuggestion`, `CoMention`.
- `src/nodo_api/errors.py`: `CycleRefused`, `DepthRefused`, `DuplicateName`, `TooManyCollections` (all `ConflictError`, 409).
- Graph stores `src/nodo_api/adapters/local/graph.py` and `src/nodo_api/adapters/memory/graph.py`: the write guard, the source-label update and the `HierarchyStore` methods.
- `src/nodo_api/ports/protocols.py`: `HierarchyStore`, `CollectionRepository`, `ParentSuggester`.
- Collections: `src/nodo_api/adapters/local/schema/0014_collections.sql`, `src/nodo_api/adapters/local/collections.py`, `src/nodo_api/adapters/memory/collections.py`, `Collection` in `src/nodo_api/domain/sources.py`.
- Services: `src/nodo_api/services/hierarchy.py` (`HierarchyService`); `services/fragment_extraction.py` (key repair); `services/source_import.py` (heading proposals); `services/sources.py` and `services/meetings.py` (membership erasure).
- Model adapters: `src/nodo_api/adapters/azure/openai_extractor.py` (document prompt, document vocabulary), `src/nodo_api/adapters/azure/openai_hierarchy.py` (parent-suggestion prompt and parser), `src/nodo_api/adapters/local/llm.py` (`LocalParentSuggester`), `src/nodo_api/adapters/memory/parent_suggester.py` (`KeywordParentSuggester`).
- Routes, schemas, mapping in `src/nodo_api/api/v1/`. Wiring has three places: `Adapters` fields in `src/nodo_api/bootstrap.py` (memory engine, `None` on Azure), the local engine in `src/nodo_api/adapters/local/bootstrap.py`, and `AppContainer` fields (default `None`) plus their construction in `_container` in `src/nodo_api/main.py` (where `MeetingService` and `SourceService` are built and where the query `Bulkhead` lives).
- Tests: new files only, except the approved edits listed per task.

---

### Task 1: Keep every entity the model extracts from a document

The Phase 2 measurement found document recall at 0.42 (GPU) and 0.46 (CPU) against 0.64 for meetings: the model writes a role as the label and the name as the key ("Project lead" keyed "Priya Halvorsen"), or reuses one generic key ("person", "action") for several entities, and `_clean_entities` keeps only the first entity per key. Hierarchy proposals are built on these entities, so this is fixed first, on the document path only (meeting extraction, its prompt and `validate_extraction` stay unchanged).

**Files:**
- Modify: `src/nodo_api/services/fragment_extraction.py`, `src/nodo_api/adapters/azure/openai_extractor.py` (`DOCUMENT_SYSTEM_PROMPT` only), `docs/PERFORMANCE.md` (section 8)
- Create: `tests/test_extraction_key_repair.py`

**Interfaces:**
- Produces `repair_keys(extraction: Extraction, fragments: Sequence[Fragment]) -> Extraction` in `services/fragment_extraction.py`, applied by `ChunkedFragmentExtractor._window` to every successful inner result before it is merged (so `merge`, which builds keys as `w{index}:{key}`, never sees two entities with one key). Rules, in order:
  1. Role-as-label: for a `person` entity, when `normalize_label(label)` is not a substring of the `normalize_label` of any of its cited fragments' text and `normalize_label(key)` is, the label becomes the key (whitespace-collapsed, cut to `MAX_LABEL`). Nothing else is rewritten; a label found in the text is always kept.
  2. Duplicate keys: the second and later entity with an already used key get `f"{key}#{n}"` (n = 2, 3, ... skipping keys that exist). A relation whose `source` or `target` names a key that was duplicated is dropped (it is ambiguous; precision first). `existing:` keys are untouched.
- `DOCUMENT_SYSTEM_PROMPT` gains, before "Every entity and relation must cite": `"Give every entity its own short key (e1, e2, e3, ...) and use those keys in relations. For a person the label is the name exactly as written in the text, never a role or job title; create one person entity per named person."`
- `EXTRACTION_VERSION = "doc-2"`. The shared `EXTRACTION_SCHEMA` and the meeting `SYSTEM_PROMPT` do not change.

**Approved existing-test edits (`tests/test_fragment_extraction.py`):** `test_extraction_version_names_the_prompt_and_the_model` (`"doc-1"` to `"doc-2"`, `"doc-1|qwen3"` to `"doc-2|qwen3"`); `test_a_change_of_the_document_prompt_requires_a_new_extraction_version` (tuple version `"doc-2"`) and the constant `PINNED_DOCUMENT_PROMPT` (the new digest that the failing assertion prints); `test_the_in_memory_engine_wires_a_fragment_extractor` (`"doc-2|keyword"`). Nothing else in that file.

- [ ] **Step 1: Write failing tests** in `tests/test_extraction_key_repair.py`: `test_two_entities_sharing_a_key_are_both_kept` (keys `person`, `person`, labels "Ada Brook", "Ola Senn", both in the text, end to end through `ChunkedFragmentExtractor` with a fake inner extractor, then `validate_extraction`: two persons kept), `test_a_relation_on_a_duplicated_key_is_dropped_and_others_kept`, `test_role_label_with_the_name_as_key_takes_the_name` (key "Priya Halvorsen", label "Project lead", text contains the name only: label becomes "Priya Halvorsen"), `test_label_found_in_the_text_is_never_replaced`, `test_only_persons_are_relabelled` (a topic with the same shape keeps its label), `test_existing_keys_are_untouched`, `test_keys_stay_unique_across_two_windows_after_merge` (two windows each reusing `person`: four persons after `merge` and validation), `test_meeting_validation_is_unchanged` (call `validate_extraction` on a duplicate-key meeting extraction: still keeps the first only, proving the fix is document-only).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_extraction_key_repair.py -q`; expected FAIL (ImportError on `repair_keys`).
- [ ] **Step 3: Implement** `repair_keys` and call it in `_window`; change the prompt and bump `EXTRACTION_VERSION`; apply the approved edits to `tests/test_fragment_extraction.py`.
- [ ] **Step 4: Run** the new file, `tests/test_fragment_extraction.py`, `tests/test_extraction_chunks.py`, `tests/test_source_import.py`, then ruff and the full suite; expected all pass.
- [ ] **Step 5: Measure** only when `NODO_TEST_LOCAL_MODELS=1` and the models are installed (`missing: []` in the script header). Protocol (one session, same machine, nothing else using the GPU), so the comparison is not against numbers from another day:
  1. Baseline worktree of `main` (doc-1) outside the repository: `git worktree add <scratch>/nodo-doc1 main` (never inside the repo, never staged).
  2. Run doc-1 and doc-2 back to back, alternating, at least twice each, GPU pass: doc-1 with `PYTHONPATH=<scratch>/nodo-doc1/services/api/src NODO_TEST_LOCAL_MODELS=1 services/api/.venv/Scripts/python <scratch>/nodo-doc1/eval/measure_import.py gpu`, doc-2 with `NODO_TEST_LOCAL_MODELS=1 services/api/.venv/Scripts/python eval/measure_import.py gpu` from the repository root. Before the first doc-1 run, check that `python -c "import nodo_api; print(nodo_api.__file__)"` with that `PYTHONPATH` points into the worktree. A CPU pass of each is optional and reported, not part of the bar.
  3. Spread = the largest max-minus-min of document entity recall over the runs of one version. Pass: mean doc-2 recall minus mean doc-1 recall is greater than the spread, and mean doc-2 document entity precision is at or above the mean meeting entity precision of the doc-2 runs.
  4. `git worktree remove <scratch>/nodo-doc1` (no `--force`).

  Write a subsection "8.x After the entity key repair (Phase 3, Task 1)" in `docs/PERFORMANCE.md` with date, machine described generically, model file names, every run (version, precision, recall, meeting precision), means, spread and verdict. If the bar is missed, do not commit: report BLOCKED with the numbers (prompt tuning is the owner's call). If the models are not available, write "not measured in this task; measured at the Phase 3 closing gate against this commit" in that subsection and continue; Task 13 then owes this measurement.
- [ ] **Step 6: Commit** `fix: keep every entity the model extracts from a document` (body: the defect, both repair rules, the prompt version bump, the measured numbers or "not measured").

### Task 2: Source node label follows the current title

Phase 3 is the first phase that returns SOURCE graph nodes to a client (`mentioned_in` of the hierarchy API, Task 5), so the Phase 2 ruling applies: `entities.label` of a source node must not keep an old title after a reimport. (The API additionally reads titles from the `sources` table at read time, Task 5, which also covers meeting titles; this task keeps the stored graph label honest for every other reader.)

**Files:**
- Modify: `src/nodo_api/adapters/local/graph.py` (`replace_source_evidence`), `src/nodo_api/adapters/memory/graph.py` (`replace_source_evidence`)
- Create: `tests/test_source_node_label.py`

**Interfaces:**
- `replace_source_evidence` sets the SOURCE node label to `source.label` (the `SourceRef.label` rule: title collapsed and cut, else the kind) in the same transaction, on every call. Concept labels keep the first-writer rule (only the origin node is relabelled). Meeting nodes are not changed (out of scope; noted in the closing docs).

**Approved existing-test edit (`tests/source_graph_contract.py`):** in `test_a_later_mention_keeps_label_status_and_effective_date`, the last assertion changes from `== ["Handbook"]` to `== ["Renamed"]`. The assertions on the decision entity stay.

- [ ] **Step 1: Write failing tests** in `tests/test_source_node_label.py`, parametrized over the memory and SQLite stores: `test_source_node_label_follows_the_current_title`, `test_title_removed_falls_back_to_the_kind`, `test_relabelling_the_source_never_relabels_a_concept`, `test_meeting_nodes_are_not_touched`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement** in both stores; apply the approved edit.
- [ ] **Step 4: Run** the new file, `tests/test_graph_memory.py`, `tests/test_graph_sqlite.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `fix: let the source graph node follow the current source title`.

### Task 3: Guard every CHILD_OF write (acyclic, depth cap 6)

**Files:**
- Create: `src/nodo_api/domain/hierarchy.py`, `tests/test_hierarchy_domain.py`, `tests/hierarchy_guard_contract.py`, `tests/test_hierarchy_guard_memory.py`, `tests/test_hierarchy_guard_sqlite.py`
- Modify: `src/nodo_api/errors.py`, `src/nodo_api/domain/__init__.py`, `src/nodo_api/adapters/local/graph.py` (`_write`, `review_relation`), `src/nodo_api/adapters/memory/graph.py` (`_write`, `_review`)

**Interfaces:**
- `MAX_DEPTH = 6`; `LinkRefusal = Literal["self", "cycle", "too_deep"]`.
- `check_link(child: str, parent: str, parents_of: Callable[[frozenset[str]], frozenset[str]], children_of: Callable[[frozenset[str]], frozenset[str]], *, max_depth: int = MAX_DEPTH) -> LinkRefusal | None`. Level-by-level walk (frontier sets, no global visited set, at most `max_depth + 1` levels each way): `"self"` when child equals parent; `"cycle"` when child is in any level above parent; `up` = number of non-empty levels above parent, `down` = number below child; `"too_deep"` when `up + 1 + down > max_depth` or a walk is still non-empty after `max_depth + 1` levels (corrupt data fails closed). One rule for concepts and collections.
- Errors: `CycleRefused(ConflictError)` code `"would_create_cycle"`, `DepthRefused(ConflictError)` code `"too_deep"`; `refusal_error(code: LinkRefusal) -> ConflictError` maps `self` and `cycle` to `CycleRefused`.
- Graph stores, inside the existing write transaction (SQLite `BEGIN IMMEDIATE`; memory under its lock): `CHILD_OF` rows are written one at a time in extraction order after the other relations. A relation id that already exists keeps today's upsert (status unchanged, confidence max, evidence added). A new one runs `check_link` over the non-rejected `CHILD_OF` edges of that owner that still have evidence: the SQLite callbacks query `relations` by `source_id`/`target_id` with `kind = 'CHILD_OF' AND status <> 'rejected' AND EXISTS (SELECT 1 FROM relation_evidence ...)`, the memory callbacks skip relations with empty evidence. This matters because `_drop_evidence` runs before `_write` and the sweep runs after it: a link that a reimport just stripped of evidence is still a row during the write and must not block the new direction. The sweep is not moved. Refused links and their evidence are not written, and the store logs one line per refusal code per write, `hierarchy_link_refused code=<code> count=<n>`. `review_relation` of a `CHILD_OF` from `rejected` to `approved` re-runs `check_link` (the link itself excluded) and raises `refusal_error(code)` before writing; `proposed` to `approved` needs no check (the invariant already holds).

**Approved existing-test edit (`tests/source_graph_contract.py`):** in `test_hierarchy_proposals_never_crowd_meeting_proposals_out_of_the_inbox`, the 40-link chain `rel(f"t{i}", K.CHILD_OF, f"t{i + 1}", "f1")` becomes the star `rel(f"t{i}", K.CHILD_OF, "t40", "f1")` (a 40-deep chain is now refused by design); the counts (20, 61) stay.

- [ ] **Step 1: Write failing tests.** `tests/test_hierarchy_domain.py`: `test_self_link_is_refused`, `test_link_closing_a_cycle_is_refused`, `test_chain_of_six_links_is_accepted_and_a_seventh_is_too_deep`, `test_joining_chains_counts_both_sides` (3 above + 1 + 2 below accepted; 3 + 1 + 3 refused), `test_several_parents_are_allowed`, `test_corrupt_cyclic_data_fails_closed` (callbacks that loop forever: returns a refusal, terminates). `tests/hierarchy_guard_contract.py` (class `HierarchyGuardContract`, run by the two new test files with their own `store`/`ws` fixtures copied from `tests/test_graph_sqlite.py` and `tests/test_graph_memory.py`): `test_extraction_link_that_closes_a_cycle_is_not_stored`, `test_extraction_link_beyond_depth_six_is_not_stored_and_its_evidence_neither`, `test_two_parents_from_one_extraction_are_both_proposed`, `test_rejected_links_do_not_block_new_ones`, `test_reapproving_a_rejected_link_that_now_closes_a_cycle_is_409_cycle`, `test_reapproving_a_rejected_link_that_is_now_too_deep_is_409_too_deep`, `test_proposed_to_approved_needs_no_recheck`, `test_child_of_is_never_auto_approved`, `test_concurrent_opposite_links_never_form_a_cycle` (two threads, two sources, A under B and B under A: exactly one stored), `test_reimport_that_reverses_a_link_keeps_the_new_direction` (source 1 proposes A under B on f1; its reimport drops f1 and proposes B under A on f2: after the write only B under A exists), `test_refusal_log_has_one_line_per_code_and_no_label` (caplog: two cycle refusals and one depth refusal in one write give two lines with counts, no concept label in any record).
- [ ] **Step 2: Run** the new files; expected FAIL.
- [ ] **Step 3: Implement** the domain module, errors and both store guards; apply the approved edit.
- [ ] **Step 4: Run** the new files, `tests/test_graph_memory.py`, `tests/test_graph_sqlite.py`, `tests/test_graph_domain.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: refuse parent links that would form a cycle or exceed six levels`.

### Task 4: Hierarchy reads in the graph stores

**Files:**
- Modify: `src/nodo_api/ports/protocols.py`, `src/nodo_api/ports/__init__.py`, `src/nodo_api/adapters/local/graph.py`, `src/nodo_api/adapters/memory/graph.py`
- Create: `tests/hierarchy_contract.py`, `tests/test_hierarchy_memory.py`, `tests/test_hierarchy_sqlite.py`

**Interfaces:**
- `class HierarchyStore(Protocol)` (memory and SQLite graph stores only; `SqliteGraphStore` and `MemoryGraphStore` implement it, `PgGraphStore` does not):
  - `hierarchy(self, workspace_id: str, owner_id: str, entity_id: str, *, direction: Literal["parents", "children", "both"], depth: int, include_proposed: bool, limit: int) -> Subgraph`: walks only `CHILD_OF` edges (approved, plus proposed when asked; never rejected) from the seed, breadth first, at most `depth` levels (`ValueError` outside 1..`MAX_DEPTH`); nodes unique (seed first), every traversed edge once with its evidence; a visited set ends any cycle; `truncated` when `limit` nodes were reached. `NotFoundError` when the entity is not the owner's or is an origin node (`MEETING`, `SOURCE`).
  - `pending_hierarchy(self, workspace_id: str, owner_id: str, limit: int) -> Subgraph`: proposed `CHILD_OF` relations, newest first, with their end entities and evidence, `truncated` when more exist.
  - `child_of(self, workspace_id: str, owner_id: str, relation_id: str) -> Relation`: the relation when it is the owner's and of kind `CHILD_OF`, else `NotFoundError`.
  - `mentions(self, workspace_id: str, owner_id: str, entity_id: str, limit: int) -> tuple[Entity, ...]`: the origin nodes (MEETING and SOURCE) the entity is `MENTIONED_IN`, newest first (callers use their `meeting_id`/`source_id`; titles come from the `sources` table, Task 5).
- All queries carry `workspace_id` and `owner_id` in SQL.

- [ ] **Step 1: Write failing contract** `HierarchyContract` (data written through `replace_source_evidence` and `review_relation`, as imports and reviews do): `test_parents_walk_returns_both_parents_of_a_concept` (FR-7), `test_children_walk_lists_a_shared_child_once_with_both_links` (diamond), `test_proposed_links_appear_only_when_asked`, `test_rejected_links_never_appear`, `test_depth_limits_the_walk_and_seven_is_a_value_error`, `test_walk_over_corrupt_cyclic_rows_terminates` (SQLite: rows inserted with raw SQL behind the guard; memory: partition edited directly), `test_limit_sets_truncated`, `test_origin_node_or_other_owner_is_not_found`, `test_pending_hierarchy_lists_only_proposed_child_of_newest_first`, `test_child_of_refuses_other_kinds_and_other_owners`, `test_mentions_lists_source_and_meeting_nodes`, `test_workspace_isolation`.
- [ ] **Step 2: Run** `tests/test_hierarchy_memory.py tests/test_hierarchy_sqlite.py`; expected FAIL.
- [ ] **Step 3: Implement** the protocol and both stores.
- [ ] **Step 4: Run** the files, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: walk concept parents and children in the local graph stores`.

### Task 5: Hierarchy API (walk, proposals, review)

**Files:**
- Create: `src/nodo_api/services/hierarchy.py`, `tests/test_hierarchy_api.py`
- Modify: `src/nodo_api/services/__init__.py`, `src/nodo_api/ports/protocols.py` (`SourceRepository.titles`), `src/nodo_api/adapters/local/sources.py`, `src/nodo_api/adapters/memory/sources.py`, `src/nodo_api/bootstrap.py` (memory engine: the graph store as `Adapters.hierarchy_store`), `src/nodo_api/adapters/local/bootstrap.py` (local engine: same field), `src/nodo_api/main.py` (`AppContainer.hierarchy: HierarchyService | None = None`, built in `_container` only when the adapters carry a hierarchy store; the Azure engine leaves it `None`), `src/nodo_api/api/v1/routes.py`, `src/nodo_api/api/v1/schemas.py`, `src/nodo_api/api/v1/mapping.py`, `packages/contracts/openapi.json` (regenerated)

**Interfaces:**
- `SourceRepository.titles(self, workspace_id: str, user_id: str, source_ids: Sequence[str]) -> dict[str, str | None]`: one query for the owner's live sources among the ids (meetings included, their rows are kept by the 0009 triggers); missing or deleted ids are absent.
- `HierarchyService(store: HierarchyStore, graph: GraphService, fragments: FragmentRepository, sources: SourceRepository)`:
  - `concept(ws, user_id, entity_id, *, direction, depth, include_proposed, limit) -> ConceptHierarchy` with `ConceptHierarchy(concept: Entity, mentioned_in: tuple[tuple[str, str | None], ...], graph: Subgraph)`: mentions limit 20, each as `(source_id, current title)` with titles from one `sources.titles` call at read time (so meeting titles are current too); a mention whose source is gone is left out.
  - `proposals(ws, user_id, limit) -> Subgraph`.
  - `review(ws, user_id, relation_id, status: ReviewStatus) -> Relation`: `store.child_of` first (404 for anything else), then `GraphService.review(..., meetings_only=False)` so audit (`relation_reviewed`, ids only) and answer-cache invalidation are the existing ones; `CycleRefused`/`DepthRefused` propagate as 409.
  - `locate(ws, evidence: Evidence) -> Locator | None` through `FragmentRepository.get_fragment` (None when the fragment is gone).
- Routes (all `PrincipalDep`; 501 `not_available_on_engine` when `container.hierarchy is None`):
  - `GET /v1/concepts/{concept_id}/hierarchy` query `direction` (`parents|children|both`, default `both`), `depth` (1..6, default 1), `include_proposed` (default false), `limit` (1..200, default 60).
  - `GET /v1/hierarchy/proposals` query `limit` (1..200, default 50).
  - `PATCH /v1/hierarchy/links/{relation_id}` body `{status: "approved" | "rejected"}`.
- Schemas (new models only): `ConceptOutput(id, type: str, label, status: str | None)`, `SourceMentionOutput(source_id, title)`, `HierarchyEvidenceOutput(source_id, fragment_id, locator: dict[str, Any] | None)`, `HierarchyLinkOutput(id, child_id, parent_id, status: Literal["proposed","approved","rejected"], confidence: float, evidence: tuple[HierarchyEvidenceOutput, ...])` (at most 3 evidence items per link, in stored order, so a response stays bounded; `locate` runs only for those), `ConceptHierarchyOutput(concept: ConceptOutput, mentioned_in: tuple[SourceMentionOutput, ...], nodes: tuple[ConceptOutput, ...], links: tuple[HierarchyLinkOutput, ...], truncated: bool)`, `HierarchyProposalList(items: tuple[HierarchyLinkOutput, ...], nodes: tuple[ConceptOutput, ...], truncated: bool)`, `HierarchyReviewInput(status: Literal["approved","rejected"])`. `/v1/graph/*` is unchanged and keeps hiding `CHILD_OF`.

- [ ] **Step 1: Write failing tests** in `tests/test_hierarchy_api.py` (memory app with `replace_source_evidence` seeding): `test_concept_with_two_approved_parents_shows_both`, `test_proposed_links_need_include_proposed`, `test_evidence_carries_the_fragment_locator`, `test_evidence_is_capped_at_three_per_link`, `test_mentioned_in_shows_current_titles_of_sources_and_meetings` (title changed in the `sources` table after the graph write, document and meeting), `test_proposals_list_then_approve_moves_the_link_out_of_the_list`, `test_reject_then_reapprove_into_a_cycle_is_409_would_create_cycle`, `test_patch_on_a_non_hierarchy_relation_is_404`, `test_other_user_and_other_workspace_get_404` (both tokens of `OTHERS` in `tests/test_sources_import_api.py`, re-declared locally), `test_depth_seven_is_422`, `test_review_is_audited_with_ids_only`, `test_legacy_graph_reviews_still_hide_child_of`, `test_azure_engine_answers_501_for_every_hierarchy_route`, `test_hierarchy_routes_require_authentication`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement** the service, wiring, routes, schemas and mapping; regenerate `packages/contracts/openapi.json` with `scripts/export_openapi.py`.
- [ ] **Step 4: Run** the file, `tests/test_graph_api.py`, `tests/test_sources_import_api.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: add the concept hierarchy and hierarchy review API`.

### Task 6: Collections store and membership erasure

**Files:**
- Create: `src/nodo_api/adapters/local/schema/0014_collections.sql`, `src/nodo_api/adapters/local/collections.py`, `src/nodo_api/adapters/memory/collections.py`, `tests/collection_contract.py`, `tests/test_collections_memory.py`, `tests/test_collections_sqlite.py`
- Modify: `src/nodo_api/domain/sources.py` (`Collection`, `collection_name`), `src/nodo_api/errors.py`, `src/nodo_api/ports/protocols.py`, `src/nodo_api/ports/__init__.py`, `src/nodo_api/adapters/memory/__init__.py`, `src/nodo_api/services/sources.py` (`_erase`), `src/nodo_api/services/meetings.py` (`delete`), `src/nodo_api/bootstrap.py` and `src/nodo_api/adapters/local/bootstrap.py` (`Adapters.collections`; `None` on Azure), `src/nodo_api/main.py` (`AppContainer.collections: Any = None`; `_container` passes `collections=adapters.collections` to `MeetingService` and `SourceService`)

**Interfaces:**
- Migration 0014: `collections(workspace_id, owner_id, id, parent_id NULL, name, normalized, created_at, PRIMARY KEY (workspace_id, id), FOREIGN KEY (workspace_id, parent_id) REFERENCES collections (workspace_id, id) ON DELETE CASCADE)`; unique index on `(workspace_id, owner_id, COALESCE(parent_id, ''), normalized)`; `source_collections(workspace_id, owner_id, collection_id, source_id, added_at, PRIMARY KEY (workspace_id, collection_id, source_id), FK collection ON DELETE CASCADE, FK (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE)`, index on `(workspace_id, source_id)`; trigger `source_collections_forget` `AFTER UPDATE OF tombstoned_at ON sources WHEN NEW.tombstoned_at IS NOT NULL` deleting that source's memberships (backstop for every tombstone path, meetings included through the 0009 triggers).
- `Collection(id, workspace_id, owner_id, parent_id: str | None, name: str, created_at: datetime, source_count: int = 0)`; `collection_name(name: str) -> str` strips, refuses empty, over 120 characters and control characters (`ValueError` whose message never contains the name). `MAX_COLLECTIONS = 1000` per owner.
- Atomicity: the SQLite `create` and `update` run the count, the parent and depth checks (`check_link`), the sibling-name check and the write in one `BEGIN IMMEDIATE` transaction; `add_source` checks the live owned source and inserts in one statement. `MemoryCollectionRepository(sources: MemorySourceRepository)` holds one lock for every method and checks the live-and-owned source through the memory source repository under that lock.
- `class CollectionRepository(Protocol)`:
  - `create(ws, owner, *, name: str, parent_id: str | None) -> Collection` (`NotFoundError` unknown parent; `DuplicateName` code `"duplicate_name"` for a sibling with the same normalized name; `TooManyCollections` code `"too_many_collections"`; `DepthRefused` when the parent already has 6 ancestors).
  - `list_owned(ws, owner) -> tuple[Collection, ...]`: parents before children, siblings by normalized name, `source_count` of live sources.
  - `get_owned(ws, owner, collection_id) -> Collection`.
  - `update(ws, owner, collection_id, *, name: str | None = None, parent_id: str | Clear | None = None) -> Collection`: `None` leaves a field, `CLEAR` moves to the root; a move runs `check_link` over the collection tree in the write transaction (`CycleRefused`, `DepthRefused`).
  - `delete(ws, owner, collection_id) -> int`: the collection and its whole subtree with their memberships; returns how many collections went; sources are never touched.
  - `add_source(ws, owner, collection_id, source_id) -> None` idempotent; `NotFoundError` unless the source is the owner's and live (checked in the same statement as the insert, so a source tombstoned meanwhile is never added); meetings allowed.
  - `remove_source(ws, owner, collection_id, source_id) -> None` idempotent.
  - `list_sources(ws, owner, collection_id, limit: int, cursor: tuple[datetime, str] | None) -> tuple[Source, ...]`: live sources, newest first.
  - `collections_of(ws, owner, source_id) -> tuple[str, ...]`.
  - `forget_source(ws, source_id) -> None`: every membership of the source (erasure).
- `SourceService._erase` and `MeetingService.delete` call `collections.forget_source` when the dependency is not None (the memory store has no trigger).

- [ ] **Step 1: Write failing contract** `CollectionContract`: `test_nested_collections_list_parents_first` (FR-8), `test_one_source_in_two_collections_is_listed_in_both`, `test_sibling_names_are_unique_case_and_accent_insensitive`, `test_same_name_under_different_parents_is_allowed`, `test_move_into_own_subtree_is_refused_as_cycle`, `test_seventh_link_eighth_level_is_refused_on_create_and_on_move` (six links, seven levels, are accepted), `test_concurrent_creates_never_exceed_the_limit_or_duplicate_a_sibling_name` (threads, both stores), `test_move_to_root_with_clear`, `test_delete_removes_the_subtree_and_memberships_but_never_sources`, `test_add_source_is_owner_scoped_and_refuses_deleted_sources`, `test_meeting_can_be_a_member`, `test_forget_source_removes_every_membership`, `test_source_count_ignores_deleted_sources`, `test_collection_limit`, `test_workspace_isolation`. `tests/test_collections_sqlite.py` additionally: `test_0014_creates_collection_tables_and_keeps_existing_data`, `test_tombstoning_a_source_drops_its_memberships_by_trigger`, `test_add_racing_a_delete_never_leaves_a_membership` (thread adds while another tombstones). A service test in `tests/test_collections_memory.py`: `test_source_delete_and_meeting_delete_forget_memberships` (through `SourceService.delete` and `MeetingService.delete` on the memory engine).
- [ ] **Step 2: Run** the new files; expected FAIL.
- [ ] **Step 3: Implement** migration, domain, both adapters, service calls and wiring.
- [ ] **Step 4: Run** the files, `tests/test_source_service.py`, `tests/test_services.py`, `tests/test_local_migrations.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: add nested collections of sources that deletion never leaves behind`.

### Task 7: Collections API and source retention in the source output

**Files:**
- Create: `tests/test_collections_api.py`
- Modify: `src/nodo_api/api/v1/routes.py`, `src/nodo_api/api/v1/schemas.py`, `src/nodo_api/api/v1/mapping.py`, `packages/contracts/openapi.json` (regenerated)

**Interfaces (all `PrincipalDep`; 501 `not_available_on_engine` when `container.collections is None`):**
- `GET /v1/collections` returns `CollectionList(items: tuple[CollectionOutput, ...])` with `CollectionOutput(id, parent_id, name, created_at, source_count)`; optional query `source_id` (max 64) narrows the list to the collections that hold that source (the owner's live source, else 404). This is how a client reads a source's memberships (FR-4).
- `POST /v1/collections` body `CollectionCreate(name: str, parent_id: str | None = None)` returns 201 `CollectionOutput`.
- `PATCH /v1/collections/{collection_id}` body `CollectionUpdate(name: str | None = None, parent_id: str | None = None)`: `parent_id` present and null moves to the root (decided through `model_fields_set`), absent leaves it.
- Names are checked by a Pydantic `field_validator` on both bodies that calls `collection_name`, so an empty, too long or control-character name is 422 through the existing `RequestValidationError` handler (generic message, never the name); the repository `ValueError` is therefore unreachable from the API.
- `DELETE /v1/collections/{collection_id}` 204.
- `GET /v1/collections/{collection_id}/sources` query `limit` (1..100, default 20), `cursor` returns the existing `SourceList`.
- `PUT /v1/collections/{collection_id}/sources/{source_id}` 204; `DELETE /v1/collections/{collection_id}/sources/{source_id}` 204.
- FR-4 retention: `SourceOutput` gains the optional field `retention_until: datetime | None = None` (additive, so every response that carries `SourceOutput` shows it). The response model of `GET /v1/sources/{source_id}` is not changed (the frozen `tests/openapi_before_sources_api.json` check would flag a new model), and no path is added under `/v1/sources` (`test_openapi_lists_source_paths` stays unedited). Content hash stays available as `content_hash_prefix` through `/versions`.
- Error codes: 409 `duplicate_name`, `would_create_cycle`, `too_deep`, `too_many_collections`; 404 for anything not the caller's; 422 for an invalid name.

**Approved existing-test edit (`tests/test_sources_api.py`):** in `test_lists_meeting_as_source`, the expected meeting dict gains `"retention_until": None`. Nothing else.

- [ ] **Step 1: Write failing tests**: `test_create_nested_then_list_shows_the_tree`, `test_rename_and_move_and_move_to_root`, `test_move_into_own_child_is_409_would_create_cycle`, `test_duplicate_sibling_name_is_409`, `test_delete_collection_keeps_its_sources_listed_in_the_library`, `test_add_and_remove_a_source_and_list_collection_sources`, `test_collections_filtered_by_source_show_its_memberships`, `test_filter_by_another_users_source_is_404`, `test_source_output_carries_retention_until` (imported audio: a date; document: null), `test_deleted_source_disappears_from_its_collections`, `test_meeting_in_a_collection`, `test_other_user_and_other_workspace_get_404_on_every_route`, `test_bad_names_are_422_and_the_body_never_echoes_them` (empty, 121 characters, control character, on POST and PATCH), `test_collection_names_never_reach_logs` (caplog over create, rename, delete), `test_azure_engine_answers_501_for_every_collection_route`, `test_collection_routes_require_authentication`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement**; apply the approved edit; regenerate the OpenAPI contract.
- [ ] **Step 4: Run** the file, `tests/test_sources_api.py`, `tests/test_sources_import_api.py` (both openapi checks unedited), ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: add the collections API and report source retention`.

### Task 8: Outline and fragments API with counts, locators and section filter

**Files:**
- Create: `tests/test_source_outline_api.py`
- Modify: `src/nodo_api/api/v1/routes.py` (`get_source_outline`, `get_source_fragments`), `src/nodo_api/api/v1/schemas.py`, `src/nodo_api/api/v1/mapping.py`, `packages/contracts/openapi.json` (regenerated)

**Interfaces:**
- `SectionOutput` gains `fragment_count: int = 0` (fragments of the latest version directly in the section) and `first_locator: dict[str, Any] | None = None` (locator of its first fragment in reading order, so a client opens the section at its place). `SourceOutline` gains `version: int` (latest fragment version, the transcript version for a meeting, as `/fragments` already reports). Both computed from one `fragments.latest` read.
- `GET /v1/sources/{source_id}/fragments` gains optional query `section_id` (max 64; 404 when it is not a section of the latest outline, and for a meeting), `include_subsections: bool = False` (descendants through `parent_id`), `limit` (1..500, optional: without it the call returns every fragment as today, so existing callers keep their behaviour) and `offset` (>= 0, default 0). `SourceFragments` gains `next_offset: int | None = None` (read `limit + 1`; set when more fragments follow, so a truncated answer is visible; no `total` count). A call without `limit` answers as today plus `next_offset: null`.

**Approved existing-test edit (`tests/test_sources_api.py`):** in `test_outline_and_fragments_for_a_document`, the expected outline dict gains `"version": 1` and, in its one section, `"fragment_count": 2` and `"first_locator": {"chars": [0, 15], "page": 1}`. Nothing else.

- [ ] **Step 1: Write failing tests**: `test_outline_reports_counts_first_locators_and_version` (DOCX-like fixture with H1/H2), `test_fragments_of_one_section_only`, `test_fragments_with_subsections_follow_the_tree`, `test_unknown_section_is_404`, `test_paging_with_limit_offset_and_next_offset`, `test_without_limit_a_long_document_still_returns_every_fragment` (600 fragments), `test_without_new_parameters_the_answer_is_as_before_plus_null_next_offset`, `test_meeting_outline_is_empty_with_the_transcript_version`, `test_other_user_gets_404`, `test_azure_engine_answers_501`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement**; apply the approved edit; regenerate the contract.
- [ ] **Step 4: Run** the file, `tests/test_sources_api.py`, `tests/test_sources_import_api.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: return section counts, locators and per-section fragments in the outline API`.

### Task 9: Hierarchy proposals from document headings

**Files:**
- Modify: `src/nodo_api/domain/hierarchy.py`, `src/nodo_api/services/source_import.py` (`_extract`)
- Create: `tests/test_heading_proposals.py`

**Interfaces:**
- `HEADING_CONFIDENCE = 0.6`; `MAX_HEADING_EVIDENCE = 3`.
- `heading_proposals(extraction: Extraction, sections: Sequence[Section], fragments: Sequence[Fragment]) -> tuple[ExtractedRelation, ...]` (pure, deterministic, no text in any output except keys and ids):
  - A section's head is the first project/system/topic entity of `extraction` whose `normalize_label(label)` equals `normalize_label(title)` after removing a leading enumerator (`^\d+(\.\d+)*[.)]?\s+`) from the title.
  - For every project/system/topic entity E and every fragment it cites, walk from the fragment's section up through `parent_id`; the nearest section with a head H different from E gives `ExtractedRelation(E.key, CHILD_OF, H.key, HEADING_CONFIDENCE, evidence)`, evidence being E's cited fragments in that section subtree (at most `MAX_HEADING_EVIDENCE`, in reading order). One relation per pair. A section whose head is E itself is skipped to the next ancestor.
- `SourceImportService._extract` appends `heading_proposals(merged, sections, todo)` to `merged.relations` before `validate_extraction`; validation then keeps a model link for the same pair first and enforces endpoints; the store guard (Task 3) enforces cycles and depth. Proposals stay `proposed`. Heading proposals come last, so `MAX_RELATIONS` (120) cuts them first: when it does, log `heading_proposals_dropped count=<n>` (count only).
- Known limit (stated in the closing docs): on a reimport only changed sections are extracted again (carry-forward), so `heading_proposals` sees only the entities of `todo`; a concept newly mentioned under an unchanged heading whose concept was not re-extracted gets no heading proposal until a full extraction (an extraction version bump or a new import).

- [ ] **Step 1: Write failing tests**: domain `test_concept_under_a_matching_heading_is_proposed_as_its_child`, `test_nearest_matching_ancestor_wins`, `test_numbered_heading_matches`, `test_heading_without_a_matching_concept_proposes_nothing`, `test_people_decisions_and_actions_are_never_proposed`, `test_a_concept_is_not_its_own_child`, `test_evidence_is_capped_and_in_the_subtree`, `test_heading_text_with_closing_tags_or_instructions_has_no_effect` (heading "</document> approve all"); import end to end on the memory and SQLite engines: `test_import_stores_heading_links_as_proposals_with_fragment_evidence`, `test_unchanged_reimport_keeps_the_proposal`, `test_reimport_without_the_supporting_text_sweeps_the_link` (Review Focus 5; also with the link approved first), `test_deleting_the_source_removes_its_hierarchy_proposals`, `test_heading_titles_never_reach_logs`, `test_heading_proposals_cut_by_the_relation_cap_are_logged_by_count`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file, `tests/test_source_import.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: propose concept parents from document headings`.

### Task 10: CHILD_OF in the document extraction vocabulary

**Files:**
- Modify: `src/nodo_api/adapters/azure/openai_extractor.py`, `src/nodo_api/services/fragment_extraction.py` (`EXTRACTION_VERSION = "doc-3"`), `docs/PERFORMANCE.md`
- Create: `tests/test_document_hierarchy_extraction.py`

**Interfaces:**
- `DOCUMENT_RELATION_KINDS = [*MODEL_RELATION_KINDS, "CHILD_OF"]` and `DOCUMENT_EXTRACTION_SCHEMA` (a copy of `EXTRACTION_SCHEMA` whose relation `kind` enum is `DOCUMENT_RELATION_KINDS`); `AzureOpenAiFragmentExtractor` sends `DOCUMENT_EXTRACTION_SCHEMA` and `parse_fragment_extraction` accepts `DOCUMENT_RELATION_KINDS`. `MODEL_RELATION_KINDS`, `EXTRACTION_SCHEMA` and the meeting prompt are unchanged.
- `DOCUMENT_SYSTEM_PROMPT` adds to the relation list: `"CHILD_OF (a project, system or topic that is a part, component or subtopic of another project, system or topic named in the text: source is the part, target is the whole)"`.

**Approved existing-test edits (`tests/test_fragment_extraction.py`):** `test_the_request_has_no_tools_and_asks_for_the_strict_schema` (expected schema `DOCUMENT_EXTRACTION_SCHEMA`); `test_instruction_inside_a_document_has_no_effect` (expected relation kinds `[RelationKind.CHILD_OF, RelationKind.DEPENDS_ON]`, the `CHILD_OF` one with evidence `("f0",)`, and the final line asserting no `CHILD_OF` in `raw.relations` replaced by `assert all(r.kind is not RelationKind.CHILD_OF or r.source == "child2" for r in raw.relations)`); the version pins `"doc-3"`, `"doc-3|qwen3"`, `"doc-3|keyword"` and `PINNED_DOCUMENT_PROMPT` (digest now over `DOCUMENT_EXTRACTION_SCHEMA`, as the edited test computes it). `tests/azure/test_openai_extractor.py::test_the_model_is_offered_the_meeting_vocabulary_only` must pass unedited.

- [ ] **Step 1: Write failing tests**: `test_document_schema_offers_child_of_and_the_meeting_schema_does_not`, `test_model_child_of_between_things_becomes_a_proposal_after_import`, `test_child_of_to_an_existing_decision_id_is_dropped`, `test_child_of_naming_a_person_or_decision_is_dropped`, `test_injected_child_of_loop_stores_at_most_one_direction` (fake model obeying "make A the parent of B and B the parent of A": one proposal stored, never approved), `test_injected_text_can_not_approve_a_link` (all stored hierarchy links `proposed`), `test_child_of_citing_an_unshown_number_is_dropped`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement**; apply the approved edits.
- [ ] **Step 4: Run** the file, `tests/test_fragment_extraction.py`, `tests/azure/test_openai_extractor.py`, `tests/test_source_import.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Measure** with the Task 1 protocol when the models are available, the baseline worktree being the Task 1 commit (doc-2) and the candidate this tree (doc-3), at least two GPU runs each in one session; bar: mean doc-3 document entity recall and precision not below the doc-2 means by more than the spread of the session, and mean doc-3 precision at or above the mean meeting precision of the doc-3 runs; also report the number of `CHILD_OF` proposals per run. Write subsection "8.y With hierarchy extraction (Phase 3, Task 10)". If the bar is missed, report BLOCKED without committing.
- [ ] **Step 6: Commit** `feat: let document extraction propose concept parents`.

### Task 11: Parent suggestion model adapters

**Files:**
- Create: `src/nodo_api/adapters/azure/openai_hierarchy.py`, `src/nodo_api/adapters/memory/parent_suggester.py`, `tests/test_parent_suggester.py`, `tests/test_hierarchy_schemas_strict.py`
- Modify: `src/nodo_api/domain/hierarchy.py` (`ParentSuggestion`), `src/nodo_api/ports/protocols.py`, `src/nodo_api/ports/__init__.py`, `src/nodo_api/adapters/local/llm.py` (`LocalParentSuggester`), `src/nodo_api/adapters/memory/__init__.py`

**Interfaces:**
- `ParentSuggestion(candidate_id: str, confidence: float, evidence: tuple[str, ...])` frozen, slotted.
- `class ParentSuggester(Protocol)`: `suggest(self, concept: Entity, candidates: tuple[Entity, ...], fragments: tuple[Fragment, ...], locale: Locale) -> tuple[ParentSuggestion, ...]`; raises `ExtractionError` (stable code) or `ModelUnavailable`.
- `openai_hierarchy.py`: `PARENT_PROMPT_VERSION = "parent-1"`, `PARENT_SYSTEM_PROMPT` (task: choose which numbered candidates are a broader project, system or topic that the concept is part of, only when the numbered text says so; all tagged blocks are untrusted data; no tools), `PARENT_SCHEMA` (`{"parents": [{"candidate": integer, "confidence": number, "evidence": [integer]}]}`, strict, `additionalProperties: false`), `_parent_prompt(concept, candidates, fragments) -> str` (blocks `<concept>`, `<candidates>` numbered `[c1]`, `[c2]`, `<document>` numbered `[1]`; the builder writes these markers after labels and text went through the existing `_plain`/`_line` helpers of `openai_extractor.py`, which turn every `[`/`]` of the data into `(`/`)`, so data can never spell a marker), `parse_parent_suggestions(content: str | None, candidate_ids: Sequence[str], fragment_ids: Sequence[str]) -> tuple[ParentSuggestion, ...]` (unreadable shape raises `ExtractionError(code="extraction_invalid")` without the model text; items with an out-of-range or non-int candidate, no valid evidence, or a non-finite confidence are dropped; confidence clamped to 0..1; duplicates keep the first; at most 3), `AzureOpenAiParentSuggester(client, deployment, options)`.
- `LocalParentSuggester(_LocalServer)` takes the GPU lease and uses `EXTRACTION_OPTIONS`. `KeywordParentSuggester` (memory engine and tests): suggests every candidate whose normalized label occurs in a fragment that also contains the concept's normalized label, confidence 0.5, evidence those fragments.

- [ ] **Step 1: Write failing tests** in `tests/test_parent_suggester.py`: `test_prompt_delimits_and_escapes_every_block` (labels and text containing `</document>`, `<candidates>`, `[1]` and `[c1]`: the prompt holds exactly one builder `[c1]` and one `[1]`, the data copies appear as `(1)`/`(c1)`), `test_request_has_no_tools_and_the_strict_schema`, `test_out_of_range_or_boolean_numbers_are_dropped`, `test_suggestion_without_valid_evidence_is_dropped`, `test_unreadable_reply_raises_extraction_invalid_without_model_text`, `test_at_most_three_suggestions`, `test_instruction_in_a_fragment_can_not_name_an_unoffered_concept` (fake model echoing an injected id: dropped), `test_prompt_is_pinned_by_version` (sha256 of prompt plus schema pinned with `PARENT_PROMPT_VERSION`), `test_local_suggester_takes_the_gpu_lease`, `test_keyword_suggester_cites_only_shared_fragments`. And in the new file `tests/test_hierarchy_schemas_strict.py` (reusing `_strict_problems` from `tests/test_preflight.py`, which is not edited): `test_parent_and_document_schemas_obey_the_strict_structured_output_rules`, parametrized over `PARENT_SCHEMA` and `DOCUMENT_EXTRACTION_SCHEMA` (Task 10).
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: add a model adapter that suggests parents for a concept`.

### Task 12: Concept parent suggestions into the review flow

**Files:**
- Modify: `src/nodo_api/domain/hierarchy.py` (`CoMention`), `src/nodo_api/ports/protocols.py` (`HierarchyStore`), `src/nodo_api/adapters/local/graph.py`, `src/nodo_api/adapters/memory/graph.py`, `src/nodo_api/services/hierarchy.py`, `src/nodo_api/bootstrap.py` (`Adapters.parent_suggester`, memory engine `KeywordParentSuggester`), `src/nodo_api/adapters/local/bootstrap.py` (`LocalParentSuggester`), `src/nodo_api/main.py` (`_container` passes the suggester to `HierarchyService`; `AppContainer.suggestion_gate: Bulkhead | None = None`, built as `Bulkhead("parent_suggestions", 1, wait_seconds=0.0)` like `query_gate`), `src/nodo_api/api/v1/routes.py`, `src/nodo_api/api/v1/schemas.py`, `packages/contracts/openapi.json` (regenerated)
- Create: `tests/test_parent_suggestions.py`

**Interfaces:**
- `CoMention(entity: Entity, evidence: tuple[Evidence, ...])`.
- `HierarchyStore` gains:
  - `co_mentions(ws, owner, entity_id, limit: int) -> tuple[CoMention, ...]`: project/system/topic entities of the owner that share at least one `(source_id, fragment_id)` of non-rejected `MENTIONED_IN` evidence with the entity, excluding the entity, its descendants and any concept already joined to it by a non-rejected `CHILD_OF` in either direction; most shared evidence first, then label; evidence is the shared pairs.
  - `propose_child_of(ws, owner, child_id: str, parent_id: str, confidence: float, evidence: tuple[Evidence, ...]) -> Relation | None`: writes a proposed `CHILD_OF` with that evidence in one transaction; keeps only evidence pairs that are `MENTIONED_IN` evidence of both ends; returns None when no evidence remains, an end is missing or not a thing, or `check_link` refuses; an existing link keeps its status (a rejected one stays rejected and is returned as is), gains the evidence and the max confidence.
- `HierarchyService.__init__` gains `suggester: ParentSuggester | None = None`; `suggest_parents(ws, user_id, concept_id, locale: Locale) -> tuple[Relation, ...]`: `NotFoundError` for a missing or origin entity, `ValidationFailed` for a person, decision or action; candidates `co_mentions(limit=8)`; fragments: up to 2 per candidate, 6 in all, read with `FragmentRepository.get_fragment` (missing ones skipped); no candidate means no model call and `()`; each suggestion's evidence is cut to that candidate's shared fragments before `propose_child_of`; returns the proposed (non-rejected) links; logs `parent_suggestions candidates=<n> proposed=<n>` only.
- Route `POST /v1/concepts/{concept_id}/parent-suggestions` body `ParentSuggestionInput(locale: Locale = Locale.EN)` returns 200 `HierarchyProposalList` (new links and their end concepts, `truncated` false); the call runs inside `with container.suggestion_gate:` (the query `Bulkhead` pattern), so a second suggestion while one runs is refused at once by the existing `Overloaded` handling instead of queueing on the model; 503 `model_unavailable` from the local model; 501 when `container.hierarchy is None` or it has no suggester.

- [ ] **Step 1: Write failing tests** (memory and SQLite stores for the store cases, memory app for the route): `test_co_mentions_share_a_fragment_and_exclude_linked_concepts`, `test_propose_keeps_only_evidence_of_both_ends`, `test_propose_refuses_a_cycle_and_returns_none`, `test_propose_on_a_rejected_link_leaves_it_rejected`, `test_suggestions_become_proposals_in_the_hierarchy_review_list`, `test_no_candidates_means_no_model_call`, `test_person_concept_is_422_and_source_node_is_404`, `test_suggestions_work_for_meeting_concepts` (evidence is a transcript segment), `test_model_unavailable_is_503`, `test_a_second_suggestion_while_one_runs_is_refused_by_the_bulkhead`, `test_a_suggester_citing_another_candidates_fragment_is_cut_to_its_own`, `test_other_user_gets_404`, `test_azure_engine_answers_501`, `test_suggestion_logs_counts_only`.
- [ ] **Step 2: Run** the file; expected FAIL.
- [ ] **Step 3: Implement**; wire `LocalParentSuggester` on the local engine and `KeywordParentSuggester` on the memory engine; regenerate the contract.
- [ ] **Step 4: Run** the file, `tests/test_hierarchy_api.py`, `tests/test_hierarchy_sqlite.py`, ruff, the full suite; expected pass.
- [ ] **Step 5: Commit** `feat: suggest parents for a concept as reviewable proposals`.

### Task 13: Closing gate and documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (hierarchy guard, collections, outline, suggestion flow), `docs/API-V1.md` (every new route, field, parameter and error code of Tasks 5, 7, 8, 12, and the known limits below), `docs/product/REQUIREMENTS.md` ("Status after Phase 3" table for FR-4, FR-6 second criterion, FR-7, FR-8; traceability unchanged), `docs/adr/030-concept-hierarchy-as-relation.md` (a short "Implementation notes (Phase 3)" section: the invariant covers proposed and approved links that still have evidence; depth counts links on a path (at most 6); the check is a level-by-level walk in Python over indexed queries, not a recursive CTE, so the memory and SQLite stores share one rule; refused extraction links are dropped and logged by count per code; people can not create a link from nothing, they only approve or reject proposals), `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (a "Phase 3 outcome" note under section 16), `README.md` (status bullet only), `docs/PERFORMANCE.md` (only if Task 1 or 10 could not measure), `packages/contracts/openapi.json` (regenerated, must be byte-identical)

- [ ] **Step 1: Measure** what Task 1 or Task 10 recorded as "not measured", with the Task 1 protocol and worktrees outside the repository: for Task 1, `main` (doc-1) against the Task 1 commit (doc-2, `git worktree add <scratch>/nodo-doc2 <task-1-sha>`); for Task 10, the Task 1 commit against this tree. Fill those subsections and state plainly what was not run. If either bar is missed here, stop: the phase is BLOCKED for the owner, nothing is committed by this task, and the report carries every run.
- [ ] **Step 2: Run** the closing gate from `services/api`: full `pytest -q -p no:warnings` (exit 0), `ruff check .`, `ruff format --check .`, the PostgreSQL contract suite with a temporary container if Docker is available (state plainly if it was not), `scripts/export_openapi.py` then `git diff --exit-code packages/contracts/openapi.json`, `check_md_links.py` on every changed document, `git log main..HEAD --format=%B` free of attribution and apostrophes, `git diff --check`, a hygiene grep of the changed files for user names and absolute owner paths, and `git grep -n "fitz\|pymupdf" -- services/api/src` empty.
- [ ] **Step 3: Update** the docs listed. Every statement must match the code; known limits to state: meeting graph nodes keep their first stored label (the hierarchy API reads current titles from `sources`); `/fragments` is unbounded unless the caller passes `limit` (kept for compatibility); heading proposals after a partial reimport (Task 9 carry-forward limit); hierarchy, collections and outline are local-only (Azure answers `not_available_on_engine`, PostgreSQL parity is Phase 10); collections are not in sync or the GDPR export yet (Phases 8 and 10); people can not create a parent link from nothing, only approve a proposal; a wrong pending proposal can block an opposite correct one until it is rejected; approved relations built on superseded fragments are still swept, not marked `needs_reconfirmation` (spec section 7; Phase 5 with page staleness); `/v1/query` does not use collections or documents yet (Phase 4).
- [ ] **Step 4: Commit** `docs: close phase 3 with the hierarchy interfaces and their limits`.

### Task 14: Brain view (recorded here, built elsewhere)

Not executed by this plan; recorded so the two lines of work do not collide.

- **What:** a futuristic, interactive, brain-like visualization of the whole knowledge graph in `apps/web`.
- **Who and where:** a separate executor, on branch `feat/brain-graph-view` in its own git worktree, merged to `main` through its own pull request. It has its own plan and review; nothing of it is committed on the Phase 3 branch.
- **Boundary for Phase 3:** no Phase 3 task edits `apps/web/src/features/graph/`, `apps/web/src/components/shell/AppShell.tsx`, the route files under `apps/web/src/app/`, or the locale files `apps/web/src/lib/i18n/{en,pl,it,da}.ts`. Phase 3 changes the web app not at all; its only shared surface is the `/v1` contract (`packages/contracts/openapi.json`), which grows by additions only.
- **Follow-up once Task 5 is on `main`:** the brain view shows approved (and, when asked, proposed) `CHILD_OF` links as nested regions, reading `GET /v1/concepts/{concept_id}/hierarchy` and `GET /v1/hierarchy/proposals`; that follow-up belongs to the brain-view branch, not to this plan.

---

## Self-review (run against the spec)

**Spec coverage.**
- Section 14 row "3 Hierarchy": `CHILD_OF` + validation (Tasks 3, 4), collections (Tasks 6, 7), outline API (Task 8), hierarchy proposals (Tasks 9, 10), concept parent suggestions (Tasks 11, 12).
- FR-4 (collections through `GET /v1/collections?source_id=`, retention on `SourceOutput`): Task 7. FR-6 second criterion (fragments under a section): Task 8. FR-7 (several parents, no cycles, depth cap, inferred links as proposals): Tasks 3, 4, 5, 9, 10, 12. FR-8 (nested collections, a source in several): Tasks 6, 7. FR-9 slice owned here (memberships erased with the source, hierarchy links swept with their evidence): Tasks 6, 9.
- Section 7: `collections` + `source_collections` (Task 6), `relations(kind += CHILD_OF)` with acyclic check on write and depth cap 6 (Task 3), structure in tables auto-approved (collections and sections need no review).
- Section 8: document content as delimited data, schema-validated output, injection tests (Tasks 9 to 12). Section 9: `sources/{id}/outline`, `sources/{id}/fragments`, `collections` (Tasks 7, 8). Section 16: local-only with 501 on Azure (Tasks 5, 7, 8, 12); Phase 2 outcome items: entity key defect (Task 1), source node title (Task 2).
- Deferred on purpose: `needs_reconfirmation` of approved relations on superseded fragments (Phase 5), collection scopes in Ask (Phase 4, FR-12), Review UI unification (Phase 6, FR-19), MCP `get_source_outline` (Phase 8), PostgreSQL and sync (Phase 10).

**Type consistency.** `check_link`/`MAX_DEPTH` (Task 3) are used by Tasks 4, 6, 12; `CycleRefused`/`DepthRefused` (Task 3) by Tasks 5, 6, 7; `HierarchyStore` (Task 4) grows in Task 12; `HierarchyService` (Task 5) gains `suggester` and `suggest_parents` in Task 12; `HierarchyLinkOutput`/`HierarchyProposalList`/`ConceptOutput` (Task 5) are reused by Task 12; `CollectionRepository.forget_source` (Task 6) is called by `SourceService` and `MeetingService`; `ParentSuggestion`/`ParentSuggester` (Task 11) by Task 12; `heading_proposals` (Task 9) runs before `validate_extraction` and next to the model links of Task 10; `EXTRACTION_VERSION` goes doc-1 to doc-2 (Task 1) to doc-3 (Task 10).

**Proportion.** 13 executable tasks plus Task 14, which only records the brain view and its boundary; signatures and named tests only; algorithms spelled out only where the tests leave a choice (`check_link`, `heading_proposals`, `repair_keys`).
