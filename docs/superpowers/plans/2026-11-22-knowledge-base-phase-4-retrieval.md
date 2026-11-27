# Knowledge Base Phase 4 (Retrieval) Implementation Plan

**Goal:** Answer questions over meetings and imported Sources with authorized hybrid retrieval,
bounded graph context, small-to-big expansion, verifiable source locators and an honest
`UNANSWERABLE` result when evidence is insufficient.

**Architecture:** Keep the existing meeting `Retriever` and document `FragmentIndex` as separate
store-level ACL boundaries. A source-neutral coordinator resolves scope before retrieval, runs the
bounded searches, performs deterministic rank fusion, expands context within one hard token budget
and hands numbered evidence to the existing local model adapter. One validator reloads every cited
Source and Fragment and accepts only quotes contained in current, authorized evidence. Existing
meeting clients remain unchanged unless they opt into source-neutral citations.

**Scope:** Local memory and SQLite engines first. Azure/PostgreSQL remains meeting-only and returns
`not_available_on_engine` for document scopes until Phase 10. Pages do not exist until Phase 5, so
`sources_only` is contract-ready but currently equivalent to all authorized Sources.

**Requirements:** FR-10 through FR-14 in `docs/product/REQUIREMENTS.md`; sections 8, 9, 12, 14 and
16 of `docs/superpowers/specs/2026-11-12-knowledge-base-design.md`; ADR-003, ADR-011, ADR-012,
ADR-026 and ADR-029.

## Global constraints

- Authorization is applied inside every retrieval and graph-store call, never after broad reads.
- `/v1` changes are additive. Legacy `/v1/query` requests remain meeting-only and keep their current
  citation projection. Non-meeting scopes require the source-neutral citation format.
- Retrieval and graph expansion are bounded: graph depth at most 2, explicit candidate/source caps,
  and one measured context-token budget.
- Retrieved text is untrusted delimited data. It cannot issue instructions or select tools.
- A citation quote must be a substring of the stored Fragment. Deleted, superseded, foreign or
  out-of-scope evidence is invalid.
- No valid citations means `UNANSWERABLE`, empty answer text and no citations. Never manufacture a
  fallback explanation.
- Cache keys include canonical scope, citation format, retrieval version and context-budget version.
- One focused commit per task, only after focused and full tests pass. Before every commit run Ruff,
  formatting and the complete API suite. Regenerate OpenAPI twice and require byte stability.
- No production content, personal paths, secrets or sensitive text in logs, tests or documentation.

## Compatibility contract

`QueryScope` has these modes:

- `legacy`: current meeting-only behavior;
- `all`: every authorized Source supported by the engine;
- `collection`: one owned Collection, resolved to Source ids before search;
- `sources`: a bounded explicit set of owned Source ids;
- `sources_only`: excludes Pages once Phase 5 adds them.

Source-neutral citations contain `source_id`, `fragment_id`, Source version and `Locator`. Meeting
citations also populate the deprecated `meeting_id`, `segment_id`, `start_ms` and `end_ms` aliases.
Old cache JSON remains readable, but a retrieval-version bump prevents reuse under Phase 4 rules.

---

### Task 1: Freeze the Phase 4 contract

**Files:** this plan; request/response examples in `docs/API-V1.md` only after implementation.

- [ ] Confirm existing query, cache, retrieval, fragment-index and graph contracts are green.
- [ ] Record caps, compatibility rules, named tests and local/cloud boundary in this plan.
- [ ] Commit the plan without product-code changes.

### Task 2: Source-neutral citations and evidence validation

**Files:** `domain/models.py`, `domain/answer_cache.py`, `services/query.py`, `ports/protocols.py`,
new `tests/test_source_evidence_validator.py`, affected cache/security tests.

- [ ] Add optional source, fragment, version and Locator fields while preserving existing Citation
  construction and legacy JSON.
- [ ] Replace transcript-only validation with owned Source/Fragment lookup and substring validation.
- [ ] Prove meeting compatibility, document locator validation, deleted/superseded rejection,
  cross-owner rejection and the exact `UNANSWERABLE` shape.

### Task 3: Ask scopes and secure resolution

**Files:** new `domain/retrieval.py`, `api/v1/schemas.py`, `api/v1/routes.py`,
`ports/protocols.py`, new `tests/test_query_scopes.py`.

- [ ] Define validated legacy/all/collection/sources/sources-only scope values and hard source caps.
- [ ] Resolve Collection membership and explicit Sources through owner-scoped repositories before
  any retrieval call.
- [ ] Return indistinguishable not-found errors for foreign and absent ids; reject a document scope
  without source-neutral citation opt-in.

### Task 4: Unified authorized retrieval coordinator

**Files:** new `services/retrieval.py`, `domain/retrieval.py`, `services/query.py`, bootstrap wiring,
new `tests/retrieval_contract.py` and `tests/test_retrieval_service.py`.

- [ ] Invoke meeting retrieval and FragmentIndex search concurrently where supported.
- [ ] Convert meeting segments to Fragments, fuse ranks deterministically, deduplicate by
  `(source_id, fragment_id)` and cap candidates.
- [ ] Keep ACL and Source filters inside adapters; one backend failure may degrade the search but
  must never broaden scope.

### Task 5: Scope graph evidence

**Files:** `ports/protocols.py`, memory and SQLite graph adapters, graph contract tests.

- [ ] Add an authorized Source-id constraint to retrieval graph reads.
- [ ] Filter evidence before an edge leaves the adapter and omit edges with no permitted evidence.
- [ ] Prove no node, edge, label, evidence id or truncation count leaks across scope or owner.

### Task 6: Versioned Section-summary storage

**Files:** `domain/sources.py`, summary protocols, migration
`adapters/local/schema/0015_section_summaries.sql`, new memory/SQLite summary adapters,
`tests/section_summary_contract.py`.

- [ ] Store Source version, model version, prompt version, text and token count as derived data.
- [ ] Reject stale-version reads and erase summaries with the Source.
- [ ] Run the shared ownership, replacement, staleness and erasure contract on memory and SQLite.

### Task 7: Generate Section summaries safely

**Files:** new `services/section_summaries.py`, generator protocol, local/model and memory adapters,
source-import wiring and focused tests.

- [ ] Generate lazily on first use and best-effort after import.
- [ ] Send delimited untrusted text with numbered Fragment ids; expose no tools.
- [ ] Treat model failure as “no summary”, never as query failure or fabricated text.

### Task 8: Small-to-big context within budget

**Files:** new `services/context.py`, retrieval domain types, `services/query.py`,
new `tests/test_context_expansion.py`.

- [ ] Assemble the matched Fragment first, then current Section summary, then nearest siblings in
  reading order.
- [ ] Enforce one configurable token budget using `estimate_tokens`; never truncate a citation quote.
- [ ] Test exact boundary, oversized Fragment, missing summary, nested Section and deterministic
  sibling ordering cases.

### Task 9: Generalize answer adapters

**Files:** memory test adapter, local LLM, Azure OpenAI LLM compatibility path, query tests.

- [ ] Accept source-neutral numbered evidence and bounded graph context.
- [ ] Validate only citation ids the model was shown; ignore injected instructions in evidence.
- [ ] Keep Azure and voice requests meeting-only and byte-compatible.

### Task 10: Scope-aware answer cache

**Files:** answer-cache domain/service and memory, SQLite and PostgreSQL JSON adapters;
cache contract and invalidation tests.

- [ ] Add canonical scope, citation format, retrieval version and context-budget version to keys.
- [ ] Read legacy meeting citation JSON and write the additive source-neutral form.
- [ ] Revalidate cached citations with the common validator; Source deletion and reimport invalidate
  every dependent entry.

### Task 11: Locator API and evaluation growth

**Files:** API schemas/mapping/routes, `packages/contracts/openapi.json`, `eval/golden_set/*`, API,
security and evaluation tests.

- [ ] Add source id, fragment id, version, kind and Locator to opted-in citations; populate legacy
  aliases only for meetings.
- [ ] Grow the validated golden set to at least 150 cases across English, Polish, Italian and Danish,
  including documents, cross-source questions, hierarchy, negation, contradiction and unsupported
  questions.
- [ ] Measure citation validity and require at least 95% verifiable citations with zero forbidden or
  deleted-data leaks.

### Task 12: Closing gate and documentation

**Files:** `docs/API-V1.md`, `docs/ARCHITECTURE.md`, `docs/PERFORMANCE.md`,
`docs/product/REQUIREMENTS.md`, the knowledge-base spec, `README.md`, OpenAPI.

- [ ] Record the Phase 4 outcome for FR-10–14, measured token budget, retrieval/answer latency and
  any revised p95 target.
- [ ] Run prompt-injection, scope, deletion, cache freshness, memory/SQLite contracts, unchanged
  Azure meeting contracts and the full API/web/static gates.
- [ ] Regenerate OpenAPI twice and require identical bytes; run documentation link checks.
- [ ] Do not close the phase unless citation precision is at least 95%, every unanswerable result has
  empty text/no citations, context stays within budget and all authorization negatives pass.

## Build order

`citations → scopes → retrieval fusion → graph scope → summaries → context expansion → answer
adapters → cache → API/evaluation → closing gate`.

The only decision deferred to measurement is the default context-token budget. Benchmark at least
two realistic values on the reference machine; choose the smaller value that retains citation and
answer quality without materially missing the existing eight-second p95 target. If the trade-off is
material, stop at the closing gate and present the measurements to the owner.
