# NODO Knowledge Base: design

Date: 2026-11-12. Status: **draft, awaiting approval**. Owner: Pierfrancesco Lijoi.
Branch: `feat/knowledge-base`. Supersedes the scope of `docs/product/blueprint.it.md` (v0.1, Android-first, meetings only).

## 1. Intent

NODO today is a private memory for meetings. The owner uses it during meetings and wants the same
engine to be a **personal knowledge base that runs entirely on their computer**: a meeting produces a
document; that document is connected to everything else already known (earlier meetings, PDFs,
slide decks, web pages, notes); the whole can be questioned, browsed as a graph and read as a wiki.

Two outside products were studied for ideas, not code: `nashsu/llm_wiki` (GPL-3.0; a wiki that an LLM
compiles and maintains from sources) and `debpalash/VoiceStudio` (AGPL-3.0; local speech tooling and
a model manager). From them NODO takes the *shape* of a few features and keeps its own principles.

Why now: the local engine, the temporal graph, the review inbox and the voice gateway already work.
The missing piece is that every record is a *meeting*; documents cannot enter, and nothing has a
parent/child structure, so connections are shallow.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Any source (meeting, document, slide deck, web page, note, audio file) enters one archive and one graph.
2. Everything is **semantic and hierarchical**: source → section → fragment, concept → sub-concept,
   collection → sub-collection. A hit on a small piece can bring its parent context.
3. Works **fully offline on one Windows machine**, installed by a person who is not a developer.
4. Ask returns cited answers across all sources, or refuses.
5. Reviewable **wiki pages** compiled from sources, exportable as an Obsidian-readable vault.
6. Useful during a live meeting (live transcript, ask while recording) and as a personal data source.
7. Local tooling from VoiceStudio's useful half: model manager with licences, dictation, spoken answers.
8. Cloud later (Azure, then AWS) through the existing ports; the local product never depends on it.

**Non-goals (this design)**

- Voice cloning, voice design, dubbing, any voiceprint or biometric speaker recognition.
- A browser extension / web clipper, calendar or Teams integration, multi-user collaboration.
- An LLM agent with shell or file-write tools (llm_wiki has one; NODO does not get one).
- Two-way sync of the Markdown vault (export is one-way in this phase).
- Mobile app work (the Expo app is frozen; it keeps working against the unchanged `/v1/meetings` view).
- Live Azure or AWS deployment, new Terraform, new cloud adapters beyond keeping existing contracts green.

**Anti-goals** (a failure even if it technically works)

- An answer or wiki claim without a quote that exists in a stored fragment.
- Anything leaving the machine in local mode, including model downloads that the user did not approve.
- Copying GPL/AGPL code into an MIT-licensed repository.
- A knowledge base that only an engineer can install.
- A graph the user cannot read as a plain list.

## 3. Constraints

- Platform: Windows 11, one laptop class machine (reference: RTX 4050 6 GB, 16 GB RAM). CPU-only must work, slowly.
- Licence: NODO uses MIT. Reuse code only from MIT/Apache/BSD projects with a notice entry. GPL/AGPL/non-commercial projects are inspiration only. Model weights carry their own licences and are shown to the user before download.
- Principles that do not change (README "Design principles"): evidence or silence; authorization before retrieval; humans decide; archive first, projections rebuildable; fail closed on durability; bound everything; one contract for two engines; accessible list before visual graph; honest about what was verified.
- Existing contract: `/v1/meetings*`, `/v1/query`, `/v1/graph/*`, sync and export keep working for current clients.
- The cloud engine (PostgreSQL, Azure AI Search) must keep passing the shared contract suites; it gains the new model after local does.

## 4. Approaches considered

| | A. Generalize the core (chosen) | B. Separate documents module | C. Markdown vault as source of truth |
|---|---|---|---|
| Idea | `Meeting` becomes one `Source` kind; evidence points at fragments of any source; add containment | New module beside meetings; merge at query time | Files are truth, DB is an index (llm_wiki style) |
| Connections | One graph, one ACL, one delete cascade | Two evidence models; links only through entity names | Good for Obsidian, weak for temporal graph |
| Cost | Touches domain, migrations, ports, contract tests | Smallest | Rewrite; breaks archive-first and the Azure twin |
| Verdict | Chosen by the owner on 2026-11-12 | Rejected: cannot give complete connections | Rejected |

The risk map from the code survey (15 files of tests encode meeting assumptions; `meeting_id` is a key in
transcripts, search, graph evidence, blobs, jobs, exports, sync, retention and the answer-cache validator)
shapes the order of work in section 14: first make the *model* source-neutral without changing behavior,
then add capability.

## 5. Vocabulary

These become `GLOSSARY.md` (the repo has none yet).

- **Workspace / owner**: unchanged.
- **Source**: one thing put into the knowledge base. Has a `kind`: `meeting`, `document`, `deck`, `web`, `note`, `audio`. Replaces "meeting" as the unit of the archive.
- **Section**: a titled part of a source (chapter, heading, slide, agenda topic). Sections nest.
- **Fragment**: the smallest citable piece of text of a source (a transcript phrase, a paragraph, a table, a slide's text). Replaces "transcript segment". Carries a **locator** (time range, page, slide, character span).
- **Concept**: a graph entity with meaning (person, project, system, topic, decision, action). Concepts may have **parents**.
- **Collection**: a user-managed folder tree that groups sources. Structural, not inferred.
- **Page**: a compiled wiki page about a concept, a source or a question. Always derived, always cited.
- **Proposal**: anything the system inferred (relation, decision, hierarchy link, page) and a person has not yet approved.
- **Evidence**: a fragment (and quote) that supports a relation or a page claim.

## 6. Requirements (revised)

IDs are stable; each gets acceptance criteria in `docs/product/REQUIREMENTS.md` (a deliverable of this work).

| ID | Requirement | Origin |
|---|---|---|
| **Sources and import** | | |
| FR-1 | Import PDF, DOCX, PPTX, XLSX, HTML, Markdown, plain text and common audio/video files by drag-and-drop, file picker and optional watched folders | llm_wiki |
| FR-2 | Re-importing an unchanged file is a no-op (content hash); a changed file creates a new version and re-derives only what changed | llm_wiki |
| FR-3 | Import is a durable queue with progress, cancel, retry (max 3) and crash recovery | llm_wiki |
| FR-4 | Every source has kind, title, collection(s), origin (path/URL/recording), language, hash, version, retention policy. `web` means a saved HTML/Markdown file or pasted text; fetching a URL is not part of this phase (a later optional task with per-fetch approval, SSRF guard and size cap) | new |
| FR-5 | Meetings keep their attestation, speaker naming and visible-recording rules. Documents, decks, web and notes need no attestation but record an origin note. Imported `audio` (may hold other people's voices) requires a lighter confirmation ("I may process this recording") stored as an audit event, and defaults to the same 30-day audio retention as meetings; documents are kept until deleted | NODO |
| **Structure** | | |
| FR-6 | Sources are parsed into a section tree and fragments; the tree is stored, not just flattened | new |
| FR-7 | Concepts support parent/child (a concept can have several parents; no cycles); hierarchy links are proposals unless structural | new |
| FR-8 | Collections are a user-managed tree; a source can sit in several | new |
| FR-9 | Deleting a source erases its fragments, index rows, graph evidence, orphaned concepts, dependent page claims and cached answers | NODO |
| **Ask and retrieval** | | |
| FR-10 | Hybrid retrieval (full text + vectors + bounded graph neighborhood) with ACL applied before retrieval | NODO |
| FR-11 | Small-to-big: a hit on a fragment can pull its section summary and siblings, within a token budget | prior art |
| FR-12 | Ask scopes: everything, a collection, chosen sources, "sources only" (no wiki pages) | llm_wiki |
| FR-13 | Citation opens the source at the locator (timestamp, page, slide) | NODO |
| FR-14 | Refusal (`UNANSWERABLE`) when evidence is missing; same validator for all source kinds | NODO |
| **Wiki and graph** | | |
| FR-15 | Compile pages (concept, source summary, saved answer) as proposals with per-claim citations; approve, edit, reject | llm_wiki |
| FR-16 | Lint: orphan concepts, uncited claims, dangling evidence, duplicate concepts, contradicting approved decisions, stale pages (source changed/deleted) | llm_wiki |
| FR-17 | Graph insights: communities with cohesion, bridge concepts, isolated or sparse areas, unexpected cross-source links | llm_wiki |
| FR-18 | Export a vault: Markdown + YAML frontmatter + `[[wikilinks]]`, one folder per kind, openable in Obsidian | llm_wiki |
| FR-19 | Review inbox handles relations, decisions, hierarchy links and pages in one queue | NODO |
| **Meetings and voice** | | |
| FR-20 | Live meeting mode: streaming local transcript, ask-while-recording, result becomes a meeting source | NODO |
| FR-21 | Dictation: push-to-talk, local speech-to-text, text lands in Ask or a note | VoiceStudio |
| FR-22 | Spoken answers: local text-to-speech with a permissively licensed voice, optional per answer | VoiceStudio |
| FR-23 | Transcribe imported audio/video with the same local pipeline | NODO |
| **Models and platform** | | |
| FR-24 | Model manager: catalog with size, licence, checksum, hardware fit, state; asks before every download; verifies checksum; removable | VoiceStudio/Jan |
| FR-25 | Hardware tiers pick default models (CPU-only, 6 GB GPU, larger) | new |
| FR-26 | One-click installer; first run guides model choice; no developer tools needed | new |
| FR-27 | Local HTTP API and read-only MCP server on loopback, same per-launch secret | llm_wiki/AnythingLLM |
| FR-28 | Backup and restore of the whole data folder; export of everything (existing GDPR export extended) | NODO |
| FR-29 | UI in en, pl, it, da; light/dark; keyboard and screen-reader complete | NODO |
| **Cloud-ready** | | |
| FR-30 | The same ports run on Azure; sync (ADR-015) carries sources, hierarchy, reviewed state and approved pages | NODO |

Quality attributes (measured, targets in section 12): grounding precision, zero cross-workspace leaks,
complete deletion, ingest time per page and per audio hour, ask latency, accessibility, offline operation.

## 7. Data model

All changes are additive migrations in `adapters/local/schema/` (0007+) and `migrations/` (0012+),
backfilled so existing data keeps its ids. Ids of meetings, segments and entities do not change.

```text
sources(workspace, owner, id, kind, title, state, origin, language, content_hash, version,
        created_at, retention_until, tombstoned_at)        -- id == meetings.id for kind='meeting'
meeting_details(source_id, attestation, speaker_names, audio_erased_at, duration_ms)  -- today's meetings extras
sections(source_id, id, parent_section_id, ordinal, level, title)
fragments(source_id, id, section_id, ordinal, text, locator_json, start_ms?, end_ms?, speaker_id?,
          confidence?, version)                              -- transcript_segments backfilled 1:1
collections(workspace, owner, id, parent_id, name)  +  source_collections(source_id, collection_id)
relation_evidence(..., source_id, fragment_id)               -- was (meeting_id, segment_id): same values
entities(..., ref)                                           -- type 'source' replaces 'meeting' (alias kept while migrating)
relations(kind += CHILD_OF)                                  -- concept hierarchy; acyclic check on write
pages(id, owner, kind, slug, title, status, body_md, content_hash, model_version, prompt_version, stale)
page_claims(page_id, ordinal, text)  +  page_citations(page_id, claim_ordinal, source_id, fragment_id, quote)
page_revisions(page_id, rev, body_md, author, at, provenance) -- human edits are authoritative and kept
page_revision_claims(page_id, rev, ordinal, span_start, span_end, text_hash) + per-revision citations
                                                             -- lets erasure redact old revisions (ADR-031)
```

**Phase 1 implementation notes.** The additive first phase deliberately keeps four transitional
forms. `MEETING` and `SOURCE` graph nodes coexist: meetings retain `MEETING`, while every other
source uses `SOURCE`. The existing `meetings` table remains the meeting-detail table and SQLite
triggers backfill and keep its shared fields in `sources`, preserving all ids. `Citation` and the
search index remain meeting/segment-shaped until Phases 2 and 4. `CHILD_OF` is available in the
graph vocabulary now, while write-time cycle enforcement and the depth-six hierarchy rules arrive
in Phase 3.

Decisions inside the model:

- **Locator** is JSON: `{"t":[start_ms,end_ms]}`, `{"page":n,"bbox":[...]}`, `{"slide":n}`, `{"chars":[a,b]}`. The evidence validator keeps its strictest rule for every kind: the quoted text must be a substring of the stored fragment. Time ranges are checked only when present.
- **Concept hierarchy as a relation (`CHILD_OF`), not a column.** It reuses review state, confidence, validity time, evidence and traversal; allows several parents; costs an acyclicity check on write and a recursive query with a depth cap (6). Source→section→fragment and collection nesting are structural tables, auto-approved.
- **Section summaries** are derived (stored with model/prompt version, rebuildable), used only for small-to-big retrieval and for source pages.
- **Pages are derived but human edits survive**: a revision authored by a person is never overwritten; regeneration produces a new proposal against it. This is the one deliberate exception to "all projections are rebuildable", and it is recorded in an ADR.
- **Versions and deletion** (rules, because they fix the id scheme):
  - A source version is immutable. A new version re-parses; a fragment whose section path and text hash are unchanged **keeps its id** and is carried forward; changed or removed fragments stay stored as "superseded" so earlier approvals remain explainable and citations show their version.
  - Relations and decisions an approved person built on a superseded fragment are not deleted: they become `needs_reconfirmation` and appear in Review. Pages that cite superseded fragments become `stale` and regenerate as proposals against the human revision.
  - **Deleting a source wins over everything.** All its versions, fragments, index rows, graph evidence, orphaned concepts and cache entries are erased. Any page claim that cited an erased fragment, even if it also cites fragments that survive, is removed from the current body *and redacted in every stored revision*, leaving a visible "removed: source deleted" marker, and the page goes `stale` so it regenerates as a proposal against the remaining sources. Human-written text is kept only where it does not depend on the erased source (uncited text depends on none). ADR-031 gives the reasons and the per-revision records this needs.
- **Source node kept in the graph** (`MENTIONED_IN` / `DECIDED_IN` target the source node) so the existing sweep and erase rules stay valid.

## 8. Pipelines

**Document import** (new): hash → dedup → parse to a structured tree (local parser) → sections →
fragments (structure-aware, token-bounded; tables and captions kept with their section) → embeddings →
extraction (entities, relations, hierarchy links, with fragment evidence; the existing `validate_extraction`
generalized) → proposals → section and source summaries → page proposals. Untrusted-text rule: document
content is passed to the model as delimited data; extraction output is schema-validated; an instruction
inside a document has no effect (prompt-injection tests are part of the suite).

**Meeting** (existing, adapted): audio → local STT → fragments (kind `meeting`) → same extraction. A
meeting also gets sections from topic segmentation (a later task; one root section until then).

**Retrieval**: query planner (scope, time, entities) → in parallel full-text, vector, graph neighborhood
(depth ≤ 2) → merge and rerank → small-to-big expansion within budget → grounded answer → validator → cache.

**Maintenance**: index verify/repair, sweeps and the answer-cache epoch (ADR-021/022/026) apply to all
kinds; lint runs on demand and weekly in the background.

## 9. Interfaces

**API** (additive, `/v1`): `sources` (create/import/list/get/delete, versions), `sources/{id}/outline`,
`sources/{id}/fragments`, `collections`, `pages` (list/get/propose/approve/edit/reject), `lint`,
`insights`, `models` (catalog, download with explicit approval, remove), `dictation`, `speech/answer`,
`vault/export`. `/v1/meetings*` stays as a filtered view of `kind='meeting'`. `Citation` gains
`source_id`, `fragment_id`, `locator` and keeps `meeting_id`/`segment_id` as deprecated aliases.

**MCP** (stdio and loopback, read-only in this phase): `search`, `get_fragment`, `get_source_outline`,
`lookup_concept`, `traverse`, `get_page`, `list_proposals`. No write tools: proposals are approved in the app.

**Wire contract**: `packages/contracts/openapi.json` regenerated and checked in CI.

## 10. Interface (UI) revision

Navigation: **Home · Library · Capture · Ask · Graph · Wiki · Review · Health · Models · Settings**.

| Screen | Change |
|---|---|
| Home | Today view: live/recent sources, review count, health findings, quick ask, quick capture/import |
| Library (replaces Meetings) | Collections tree + source list; filter by kind, date, language; drag-and-drop import; per-source state and progress (Activity panel) |
| Source detail | Kind-specific viewer (transcript with speakers / document outline and text / slide list) with an **outline panel** (section tree) and a **connections panel** (concepts, related sources, pages); citation links open at the locator |
| Capture | Meeting live mode (notice + attestation unchanged), live transcript, ask-while-recording; dictation entry point |
| Ask | Scope picker, "sources only" toggle, answer with citations by kind, spoken answer button, save answer as page proposal |
| Graph | Accessible **hierarchy tree/list first**, map second; filter by kind/collection/time; communities and bridge concepts layer |
| Wiki | Page tree, page reader with per-claim footnotes, proposal view with diff, approve/edit/reject, vault export |
| Review | One inbox: relations, decisions, hierarchy links, page proposals |
| Health | Lint findings and graph insights, each with a fix action that creates a proposal or opens the item |
| Models | Catalog, size, licence, hardware fit, install/remove, disk use |

Design system, tokens, accessibility rules and the four languages stay as in `docs/DESIGN.md`.
Wireframes for Library, Source detail, Wiki and Models are produced as a task (section 14) and reviewed
before the screens are built.

## 11. Local-first platform

- **Engine**: unchanged hexagonal Python/FastAPI with local adapters (SQLite + FTS5 + exact vector search, Whisper, llama.cpp, ONNX embeddings, sherpa-onnx diarization).
- **Parsing**: Docling (MIT) as primary parser and chunker, MarkItDown (MIT) as light fallback; each parser model weight is audited in the model catalog. Picked from the prior-art survey; a spike confirms CPU/GPU behavior and size on the reference machine before it is committed to.
- **Speech**: keep faster-whisper for batch; sherpa-onnx for diarization/VAD (already in use). Streaming policy ideas from WhisperLiveKit (Apache-2.0) may inform live mode.
- **TTS**: kokoro-onnx (MIT code, Apache weights) behind a `TextToSpeech` port; Piper's new GPL code and espeak-ng are excluded. Voices are catalog entries with licences.
- **Dictation**: push-to-talk modelled on Handy (MIT, idea only); Windows global hotkey, local STT, text inserted into the app (first) or the focused window (later, opt-in).
- **Model manager**: catalog JSON (id, files, sizes, sha256, licence name and URL, hardware needs, tasks); UX pattern from Jan/Lemonade; no download without an explicit yes; resumable; checksum verified; reproducible offline install from a folder.
- **Packaging**: a signed Windows installer bundling the frozen engine (PyInstaller, with its GPL bootloader exception noted in NOTICE), the static web export, llama.cpp, ffmpeg (build and licence recorded) and a first-run wizard. The shell is either today's Edge `--app` window or a Tauri v2 sidecar app; **decided by a spike** (size, startup, WebView2 microphone permission, update path). Meetily, Handy and Jan are the reference points.
- **Data**: `%LOCALAPPDATA%\NODO` as today; backup is a single folder copy plus an export command; database migrations are forward-only with an automatic pre-migration copy.

## 12. Quality and verification

No claim is made before it is measured on the reference machine.

- **Contract suites** (`graph_contract`, `answer_cache_contract`, `entity_contract`, `review_contract`) are extended with source-neutral cases and run on memory, SQLite and PostgreSQL. A migration task is not done until all three are green.
- **Golden set** (`eval/golden_set`, 60 cases, 4 languages) is extended with document and cross-source cases (target: at least 150), including negations, contradicted decisions, hierarchy questions and unanswerable questions.
- **Targets** (to be confirmed or revised after the first measurement): citation precision ≥ 95 % verifiable; zero cross-workspace and deleted-data leaks; deletion test proves absence from fragments, index, graph, pages, cache; ask p95 ≤ 8 s on the reference machine (existing target); ingest time per 50-page PDF and per audio hour recorded in `docs/PERFORMANCE.md`; installer smoke test on a clean Windows VM.
- **Security tests**: prompt injection through documents and web pages; path traversal and zip-bomb handling in import; MCP and local API cannot be reached from a non-loopback host or via DNS rebinding (existing guard kept); no new outbound network call in local mode (a test fails the build if one appears).
- **Accessibility**: axe in the browser suite for every new screen; keyboard-only paths for import, review and graph.
- **Static gates** before every commit: ruff, mypy/pyright as configured, pytest for touched areas, web typecheck/lint/vitest; full suites before each phase closes.

## 13. Cloud path (after local)

- Order: local first; then PostgreSQL adapters and migrations for sources, fragments, sections, pages; Azure AI Search index fields change from `meeting_id` to `source_id`/`fragment_id`; sync carries the new objects.
- AWS: an ADR with the port-by-port mapping (S3, SQS, RDS/Aurora PostgreSQL + pgvector or OpenSearch, Bedrock, Transcribe, Cognito) and no code until the local product is accepted. Nothing in the domain or ports may be named after a provider.
- Honest status: nothing in this design is verified on a live cloud subscription, and this work does not change that.

## 14. Work breakdown

One commit per task, each with an explained message, no tool attribution, made only after the task's
checks pass. Detailed steps are produced by the planning step after approval.

| Phase | Tasks (each a commit) |
|---|---|
| 0 Foundation | branch, `GLOSSARY.md`, ADR-029 (source model), ADR-030 (hierarchy as relation), ADR-031 (pages and human edits), licence hygiene and `NOTICE`, requirements document, revised product document |
| 1 Source-neutral core | migrations + backfill, domain (`Source`, `Fragment`, `Locator`, `Evidence`), ports, memory + SQLite adapters, contract suites, `/v1/sources` view over meetings, confirm the unchanged PostgreSQL adapters still pass the shared contracts |
| 2 Documents | parser spike, import queue, section/fragment builder, versioning, extraction generalization, library API, watched folders |
| 3 Hierarchy | `CHILD_OF` + validation, collections, outline API, hierarchy proposals, concept parent suggestions |
| 4 Retrieval | small-to-big, scopes, citation locators, evaluation set growth, answer-cache key update |
| 5 Wiki and health | page compiler, claim validator, revisions, lint, community/insight analysis, vault export |
| 6 UI | wireframes, Library, Source detail, Wiki, Health, Graph hierarchy, Review unification, Models, i18n x4, accessibility tests |
| 7 Voice and models | model manager, hardware tiers, dictation, TTS port + adapter, live meeting mode polish |
| 8 Integration | local API additions, MCP server, backup/restore |
| 9 Packaging | shell spike, installer, first-run wizard, clean-VM smoke test |
| 10 Cloud catch-up | Azure adapters for the new model, sync extension, AWS ADR |

Phases 0 to 5 deliver the knowledge base on the current desktop setup; 6 to 9 make it a product a
non-engineer can install; 10 follows.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Generalizing breaks meeting behavior | Phase 1 changes the model with no behavior change; contract suites on all three stores gate every commit |
| Small local LLM extracts weak hierarchy | Hierarchy stays a proposal; deterministic structure (headings, folders) carries most of the tree |
| Doc parsing is heavy on a 6 GB machine | Parser spike with CPU fallback and a lighter converter; models in the catalog with sizes |
| Prompt injection from imported files | Delimited data, schema validation, no tools, tests |
| Wiki pages drift from sources | Per-claim citations, staleness on source change, lint |
| Scope too large | Phases ship independently; each phase leaves the product working |
| Cloud adapters lag | Contract tests keep both engines honest; cloud catch-up is its own phase |

## 16. Calls made without the owner (to confirm)

Settled after the independent builder check:

- **Cloud engine in Phase 1.** Ports evolve *additively* (new methods and types; meeting-keyed ones stay). For `kind='meeting'` the source id equals the meeting id, so the existing PostgreSQL/Azure adapters keep passing the shared contract suites unchanged and CI stays green. Documents, hierarchy and pages are **local-only until Phase 10**; the Azure engine reports them as unavailable. New contract cases carry a marker and run on memory and SQLite first.
- **Local model.** The current Qwen3-4B on llama.cpp stays the default for extraction, summaries and pages. Extraction runs batched per section; section and source summaries are lazy (on first need) plus background. The parser/extraction spike must show, on a small annotated document set, extraction precision no worse than today's meeting extraction and an ingest time recorded per 50 pages for GPU and CPU; below that, the feature ships behind a "slow on this machine" notice rather than being dropped.
- **OCR** for scanned PDFs and images is out of this phase: such files are detected, imported as "no text layer" and listed in Health. An OCR model becomes a catalog entry and a later task; the `page`+`bbox` locator is implemented for text-layer PDFs only.
- **Web sources** are files or pasted text (see FR-4); no network fetch in this phase.
- **Citation compatibility.** The frozen Expo app keeps seeing only meeting citations (`meeting_id`, `segment_id`) from `/v1/query`; document citations are returned in new fields that clients must opt into with a query parameter.
- **Plan depth.** The plan covers Phases 0 and 1 task by task; each later phase is planned in detail when the previous phase closes, because spike results change it. UI wireframe work (Phase 6) may start in parallel with Phase 2.
- Parsers, TTS engine and Tauri-vs-Edge shell are *candidates* from the survey, decided by spikes.
- `CHILD_OF` as a relation rather than a column.
- `Source` keeps `id == meeting.id` for meetings to avoid any id migration.
- Mobile app frozen; no Neo4j (the graph stays in SQLite/PostgreSQL as in ADR-012).
- MCP is read-only in this phase.
- Documentation language: English; chat and review in Italian.

## 17. Left to the builder

Exact column types and indexes; chunk sizes and token budgets (measured, not guessed); prompt texts;
catalog contents per hardware tier; installer tooling details; wording of UI strings (four languages).
