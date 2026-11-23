# Knowledge base Phase 5 — Wiki and health implementation plan

> **Status:** planned. Phase 4 remains open: its retrieval-quality gate is independent of this
> phase and must not be weakened or bypassed here.

## Goal

Deliver the local-only Wiki and Health capabilities described by FR-9 and FR-15 through FR-18.
Pages are reviewable proposals, never autonomous facts. Every machine-generated claim has
validated fragment citations; human revisions are authoritative; erasure of a source wins over
pages and their entire revision history.

This plan implements [ADR-031](../../adr/031-wiki-pages-and-human-edits.md) without adding
Azure/PostgreSQL parity (Phase 10) or a review UI (Phase 6).

## Non-negotiable invariants

1. No Page is stored before claim validation and the source-erasure redaction path both exist.
2. A proposal may contain only claims whose citations pass the existing `EvidenceValidator` rules.
3. Approving or editing creates an immutable numbered revision, except that source erasure may
   redact dependent text in place; revisions never silently disappear.
4. A claim citing an erased fragment is removed from the current body and redacted in **every**
   revision, even if it also had surviving citations. If span mapping is uncertain, redact the
   whole revision.
5. Uncited human prose is preserved but reported by lint. Model-produced uncited prose is never
   proposed.
6. Superseded, non-erased evidence marks a Page stale; it does not erase the Page or its history.
7. Every read and mutation is workspace/owner authorized before looking up page text or evidence.
8. Pages, health and export are `not_available_on_engine` outside memory/SQLite until Phase 10.

## Task 1 — establish contracts and the source-deletion seam

**Files:** `services/api/tests/`, `services/api/src/nodo_api/services/`, source deletion route
and service modules.

- Trace the current local source-delete transaction, graph/index/cache cleanup and superseded
  fragment lifecycle.
- Add failing cross-adapter contract tests for Page authorization, proposal visibility and the
  page-erasure hook. Tests must prove no dangling page citation remains after deletion.
- Add a transaction-level port or lifecycle service so source deletion can invoke page redaction
  before fragment records are removed; do not implement a best-effort background cleanup.

**Gate:** existing deletion tests stay green; new tests fail before the implementation exists.

## Task 2 — domain model and additive ports

**Files:** `domain/pages.py`, `domain/health.py`, `ports/protocols.py`, package exports and
contract fixtures.

- Add `PageKind` (`concept`, `source_summary`, `saved_answer`), `PageStatus` (`proposed`,
  `approved`, `rejected`, `stale`), `Page`, `PageProposal`, `PageClaim`, `PageCitation`,
  `PageRevision`, and explicit provenance structures.
- A claim stores ordinal, text, current-body span, text hash and one or more source-neutral
  citations. Revision claims carry the same data per revision.
- Define ports for transactional Page persistence, proposal/revision reads, source lifecycle
  redaction, lint findings, graph insights and vault export. Preserve all existing meeting ports.

**Gate:** pure-domain tests cover state transitions, immutable identity and invalid span/citation
construction without requiring SQLite.

## Task 3 — memory and SQLite storage with migrations

**Files:** memory adapters, local repositories, `adapters/local/schema/0016_*.sql`, bootstrap,
contract suites.

- Create tables for pages, current claims/citations, revisions, revision claims/citations and
  indexes by workspace, status, cited source/fragment and page kind.
- Implement memory and SQLite repositories with identical authorization and ordering semantics.
- Ensure proposal replacement creates no revision; approval/edit append revisions; rejected
  proposals are retained only until explicit cleanup/erasure rules require removal.
- Add migration tests, transaction rollback tests and memory/SQLite contract tests.

**Gate:** migrations apply on an existing Phase 4 database; shared local contracts pass.

## Task 4 — compiler and claim validator

**Files:** `services/pages.py`, LLM port/adapter additions, query validator reuse, tests.

- Compile Concept, Source-summary and saved-answer inputs into structured claim candidates:
  claim text plus numbered evidence references. A compiler must never submit raw Markdown as an
  unstructured claim list.
- Build citations from retrieved fragments, then validate every cited quote and ownership using
  the same evidence rules as answers. Drop a claim when any required support cannot be validated;
  do not ask the model to repair it.
- Render Markdown only after validated claims are chosen. Reject an empty proposal.
- Version compiler/prompt metadata and record it as proposal provenance.

**Gate:** prompt-injection, fabricated quote, cross-workspace and unsupported-claim tests prove
that no invalid proposal can persist.

## Task 5 — proposal, approval, edit and refresh API

**Files:** API schemas/routes/OpenAPI, `services/pages.py`, API tests and documentation.

- Expose owner-authorized endpoints to create/list/read proposals, approve, reject, edit, and
  request refresh. All mutation commands use idempotency keys consistent with existing routes.
- Approval writes revision 1 (or the next revision); edits append a human-authored revision and
  require explicit citations for source-dependent prose. Refresh creates a sibling proposal and
  never overwrites the current revision.
- Return citations with locators and surface stale/proposed status. Do not build UI here.

**Gate:** OpenAPI is regenerated; API tests prove an owner cannot read or mutate another
workspace’s page and that approval/edit history is stable.

## Task 6 — source supersession and deletion redaction

**Files:** source version/delete services, Page lifecycle service, repositories and tests.

- On changed/superseded evidence, find dependent Pages and mark them stale atomically.
- On source deletion, remove pending/rejected proposals that cite it; remove every affected claim
  from current bodies; redact the matching span in every revision with `removed: source deleted`.
- Recalculate later spans/text hashes in the same transaction. If an expected span or hash does
  not match, replace the entire revision body with the marker (fail closed).
- Continue existing index, graph, orphan-concept and answer-cache cleanup in the same deletion
  operation. Delete a source Page that contains only removal markers.

**Gate:** integration tests inspect current and historical revisions after deletion and prove
deleted text cannot be retrieved through Page, search, export or cache paths.

## Task 7 — health lint and graph insights

**Files:** `services/health.py`, graph/page repositories, API routes, tests.

- Add deterministic lint findings for orphan Concepts, uncited human claims, dangling Evidence,
  duplicate Concepts, contradictory approved decisions and stale Pages.
- Add best-effort graph insight analysis: communities/cohesion, bridge concepts, sparse areas and
  unexpected cross-source links. Insights are observations, not facts or relations.
- Make scans bounded, paginated and owner scoped; persist only findings with reproducible input
  versions or recompute them on request.

**Gate:** fixtures exercise every lint rule and show that deleting a source clears dependent
findings rather than leaving stale identifiers.

## Task 8 — Obsidian vault export

**Files:** `services/vault_export.py`, local export adapter/route, tests and docs.

- Export approved/stale current pages, concepts and source summaries as Markdown with YAML
  frontmatter, safe filenames and `[[wikilinks]]`; never export pending/rejected proposals.
- Include provenance/status and citable source locators without embedding deleted text. Export is
  workspace scoped and writes to an explicitly selected, validated directory.
- Provide a manifest to make incremental runs and deletion checks auditable.

**Gate:** generated vault opens as ordinary Markdown, contains no forbidden proposal/deleted
content and passes deterministic snapshot tests.

## Task 9 — verification, documentation and release gate

**Files:** contract/API/integration tests, `docs/`, `README.md`, OpenAPI.

- Run full API tests, local migration tests, lint/export integration suite, formatting/lint and
  Markdown-link validation.
- Document local-only scope, proposal/revision semantics, redaction residual limits for preexisting
  backups/exports, and the Phase 10 cloud gap.
- Measure page compiler reliability on a cited fixture set. Any invalid citation, authorization
  leak or deleted-text leak blocks the phase; quality failures cannot be reclassified as warnings.
- Create a PR only after every listed gate passes. Phase 4’s retrieval quality report remains an
  independent release blocker for that phase.

## Deferred deliberately

- Unified Review inbox and all Page/Health screens: Phase 6.
- Page use in all-query scope: only after proposal/revision authorization and retrieval evaluation
  have an explicit design update.
- Azure/PostgreSQL adapters and sync: Phase 10.
- Backup/restore extension for authoritative revisions: Phase 8.

