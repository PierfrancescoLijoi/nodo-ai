# Knowledge Base Requirements

## Purpose and interpretation

This document defines the observable product requirements for the NODO knowledge base. It uses the vocabulary in `GLOSSARY.md`: a Source contains Sections and Fragments, a Locator identifies a Fragment's position, Concepts form reviewed knowledge, Collections group Sources, Pages compile cited knowledge, and Evidence supports inferred claims.

Priority follows MoSCoW: **Must** is required for the usable knowledge base or a stated trust guarantee; **Should** is required for the planned desktop product but does not block the core knowledge base; **Could** is a planned enhancement that can follow the core experience; **Won't** is explicitly outside this delivery. Phase identifies the first phase in which the complete requirement is delivered, not the first phase that lays groundwork for it.

## Summary

| ID | Statement | Priority | Delivering phase |
|---|---|---|---|
| FR-1 | Import PDF, DOCX, PPTX, XLSX, HTML, Markdown, plain text, and common audio/video files through drag-and-drop, a file picker, or optional watched folders. | Must | Phase 2 - Documents |
| FR-2 | Treat an unchanged re-import as a content-hash no-op; create a new Source version for changed content and re-derive only changed material. | Must | Phase 2 - Documents |
| FR-3 | Run import through a durable queue with progress, cancellation, at most three retries, and crash recovery. | Must | Phase 2 - Documents |
| FR-4 | Record each Source's kind, title, Collections, origin, language, hash, version, and retention policy; accept web material only as saved or pasted content in this phase. | Must | Phase 3 - Hierarchy |
| FR-5 | Preserve meeting attestation, speaker naming, and visible-recording rules; record origin notes for other textual Sources and audited processing confirmation plus 30-day audio retention for imported audio. | Must | Phase 2 - Documents |
| FR-6 | Parse and store each Source as a nested Section tree containing citable Fragments rather than only flat text. | Must | Phase 2 - Documents |
| FR-7 | Represent acyclic Concept parent/child links with multiple parents, with inferred links held as Proposals and structural links authoritative. | Must | Phase 3 - Hierarchy |
| FR-8 | Provide an owner-managed Collection tree in which a Source can belong to several Collections. | Must | Phase 3 - Hierarchy |
| FR-9 | Deleting a Source erases its Fragments, index rows, graph Evidence, orphaned Concepts, dependent Page claims, and cached answers. | Must | Phase 5 - Wiki and health |
| FR-10 | Combine full-text, vector, and bounded graph-neighborhood retrieval, applying workspace authorization before retrieval. | Must | Phase 4 - Retrieval |
| FR-11 | Allow a Fragment hit to pull its Section summary and sibling context within a token budget. | Must | Phase 4 - Retrieval |
| FR-12 | Support Ask over everything, one Collection, selected Sources, or Sources only without Pages. | Must | Phase 4 - Retrieval |
| FR-13 | Open every answer citation at its Source Locator, including timestamp, page, or slide. | Must | Phase 4 - Retrieval |
| FR-14 | Return `UNANSWERABLE` when Evidence is missing and use the same Evidence validator for every Source kind. | Must | Phase 4 - Retrieval |
| FR-15 | Compile Concept, Source-summary, and saved-answer Pages as Proposals with per-claim citations that an owner can approve, edit, or reject. | Must | Phase 5 - Wiki and health |
| FR-16 | Lint orphan Concepts, uncited claims, dangling Evidence, duplicate Concepts, contradicting approved decisions, and Pages made stale by changed or deleted Sources. | Should | Phase 5 - Wiki and health |
| FR-17 | Report graph communities with cohesion, bridge Concepts, isolated or sparse areas, and unexpected cross-Source links. | Could | Phase 5 - Wiki and health |
| FR-18 | Export an Obsidian-readable vault using Markdown, YAML frontmatter, `[[wikilinks]]`, and one folder per kind. | Should | Phase 5 - Wiki and health |
| FR-19 | Present relations, decisions, hierarchy links, and Pages in one review inbox. | Must | Phase 6 - UI |
| FR-20 | Provide live meeting mode with a streaming local transcript, Ask while recording, and a resulting meeting Source. | Should | Phase 7 - Voice and models |
| FR-21 | Provide push-to-talk dictation using local speech-to-text, placing text into Ask or a note. | Could | Phase 7 - Voice and models |
| FR-22 | Optionally speak an individual answer locally with a permissively licensed voice. | Could | Phase 7 - Voice and models |
| FR-23 | Transcribe imported audio and video with the same local pipeline used for meetings. | Must | Phase 2 - Documents |
| FR-24 | Provide a removable model catalog showing size, licence, checksum, hardware fit, and state; require approval before every download and verify its checksum. | Should | Phase 7 - Voice and models |
| FR-25 | Select default models for CPU-only, 6 GB GPU, and larger-hardware tiers. | Should | Phase 7 - Voice and models |
| FR-26 | Provide a one-click installer and first-run model guidance without requiring developer tools. | Should | Phase 9 - Packaging |
| FR-27 | Expose a loopback-only local HTTP API and read-only MCP server protected by the same per-launch secret. | Should | Phase 8 - Integration |
| FR-28 | Back up and restore the complete data folder and extend the existing data export to include everything. | Must | Phase 8 - Integration |
| FR-29 | Provide English, Polish, Italian, and Danish UI; light and dark themes; and complete keyboard and screen-reader operation. | Should | Phase 6 - UI |
| FR-30 | Run the same ports on Azure and synchronize Sources, hierarchy, review state, and approved Pages. | Could | Phase 10 - Cloud catch-up |

## Acceptance criteria

### FR-1 Source import channels and formats

- Given a supported PDF, DOCX, PPTX, XLSX, HTML, Markdown, text, audio, or video file, when the owner imports it with drag-and-drop or the file picker, then a Source appears and, once processing finishes, shows a completed import state.
- Given a supported file that cannot be parsed, when the owner imports it, then the import shows a failed state with a reason and no partial Source content is searchable.
- Given an enabled watched folder, when a supported new file appears there, then one import is queued and its progress is visible.

### FR-2 Content-aware re-import

- Given an imported file, when the same bytes are imported again, then no new version and no new job are created, and the response says `unchanged`.
- Given an imported file whose bytes have changed, when it is imported again, then the Source gains one immutable version and unchanged Fragments retain their identifiers while changed material is re-derived.

### FR-3 Durable import queue

- Given a queued import, when processing advances, is cancelled, or fails, then its durable state and progress are visible to the owner.
- Given an interrupted or transiently failing import, when the application restarts or retry runs, then work resumes without duplication, and a failing step is retried at most three times after the first attempt before it is marked failed.

### FR-4 Source metadata and web boundary

- Given a stored Source, when its details are opened, then kind, title, Collections, origin, language, content hash, version, and retention policy are displayed or available through the API.
- Given a URL alone, when the owner attempts to create a web Source in this phase, then no network fetch occurs; saved HTML, Markdown, or pasted text can be imported instead.

### FR-5 Capture and retention controls

- Given a meeting Source, when capture begins, then the existing attestation, speaker naming, and visible-recording controls remain required and auditable.
- Given imported audio, when processing is requested, then processing waits for the recorded confirmation "I may process this recording" and the Source receives the meeting-equivalent 30-day audio retention by default.
- Given a document, deck, web, or note Source, when it is stored, then no attestation is requested, an origin note is recorded, and it remains until the owner deletes it.

### FR-6 Stored Source structure

- Given a Source with nested headings or slides, when parsing completes, then its ordered, nested Section tree can be retrieved.
- Given a parsed Section, when its contents are opened, then its citable Fragments and Locators are returned under that Section rather than only as a flat Source-wide list.

### FR-7 Concept hierarchy

- Given a Concept, when two valid parent links are approved, then both parents are visible in hierarchy traversal.
- Given a proposed parent link that would create a cycle or exceed the hierarchy depth cap, when it is submitted, then it is rejected and no link is stored.
- Given an inferred parent link, when extraction completes, then it appears as a Proposal until a person approves or rejects it.

### FR-8 Collections

- Given owner-created nested Collections, when the tree is listed, then each Collection appears under its selected parent.
- Given one Source assigned to multiple Collections, when each Collection is opened, then the same Source appears in each without being duplicated in the archive.

### FR-9 Complete Source deletion

- Given a Source used by retrieval, graph relations, Pages, and cached answers, when the owner deletes it, then its Fragments, index rows, Evidence, and cache entries are absent from subsequent reads.
- Given deletion leaves a Concept with no remaining Evidence, when cleanup completes, then the orphaned Concept is absent.
- Given a Page claim that cites the deleted Source, when deletion completes, then the claim is removed from the current Page body even if it also cites a surviving Fragment, and the Page is marked stale; the same claim is redacted in every stored revision, human revisions included, while uncited human text is kept.
- Given a revision whose claim spans cannot be mapped, when deletion completes, then the whole revision body is redacted. Pending and rejected Proposals citing the deleted Source are deleted.

### FR-10 Authorized hybrid retrieval

- Given an authorized query, when retrieval runs, then the ranked candidates combine full-text, vector, and graph-neighborhood results within configured bounds.
- Given material in another workspace, when retrieval runs, then that material is excluded before ranking and no result or citation exposes it.

### FR-11 Small-to-big context

- Given a matched Fragment, when context expansion is enabled, then its Section summary and relevant sibling Fragments can accompany it.
- Given expansion candidates beyond the configured token budget, when context is assembled, then the token count of the assembled context is at or below the budget.

### FR-12 Ask scopes

- Given a Collection or selected Sources as the Ask scope, when a question is submitted, then every returned citation belongs to that scope.
- Given `sources only` is selected, when a question is submitted, then no Page contributes to retrieval or appears as a citation.
- Given the everything scope, when a question is submitted, then authorized Sources and approved Pages across the workspace are eligible.

### FR-13 Citation navigation

- Given a citation to a meeting Fragment, when it is opened, then the Source viewer seeks to the cited timestamp.
- Given a citation to document or deck material, when it is opened, then the viewer opens the cited page or slide and identifies the cited Fragment.

### FR-14 Evidence-or-silence answers

- Given no sufficient Evidence for a question, when Ask completes, then the response is `UNANSWERABLE` and contains no unsupported answer text.
- Given Evidence from any Source kind, when it is validated, then each quote must be a substring of its stored Fragment or the citation is rejected under the same rule.

### FR-15 Cited Page proposals

- Given sufficient Evidence for a Concept, Source summary, or saved answer, when compilation runs, then a proposed Page is created with at least one validated citation per generated claim.
- Given a Page Proposal, when the owner approves or edits it, then a new revision with the person as author is added to the revision history.
- Given a Page Proposal, when the owner rejects it, then its status becomes rejected and no revision is added.
- Given an approved human revision and later regeneration, when a new compilation completes, then the human revision remains unchanged and a separate Proposal is shown.

### FR-16 Knowledge lint

- Given orphan Concepts, uncited claims, dangling Evidence, or duplicate Concepts, when lint runs, then a separate finding identifies each affected object and its finding type.
- Given an approved decision is contradicted or a cited Source changes or is deleted, when lint runs, then it reports the contradiction or stale Page and links to the affected item.

### FR-17 Graph insights

- Given a graph with connected clusters and bridge Concepts, when insight analysis runs, then each community includes a cohesion value and each detected bridge is listed.
- Given isolated, sparse, or unexpected cross-Source connections, when analysis runs, then the findings identify the affected Concepts or Sources and the supporting links.

### FR-18 Vault export

- Given approved Pages, when vault export completes, then the output contains Markdown files for approved Pages with YAML frontmatter and `[[wikilinks]]`, grouped in folders by kind.
- Given the exported folder, when it is opened as an Obsidian vault, then the Markdown files and internal links are readable without conversion.

### FR-19 Unified review inbox

- Given pending relation, decision, hierarchy-link, and Page Proposals, when Review is opened, then all types appear in one filterable queue.
- Given an item in that queue, when the owner approves or rejects it, then its review state changes and the queue count updates.

### FR-20 Live meeting mode

- Given an attested live meeting, when speech is captured, then a local transcript appears incrementally while recording continues.
- Given a live transcript, when the owner asks a question during recording, then an Evidence-bound answer or `UNANSWERABLE` is returned without stopping capture.
- Given recording ends, when finalization completes, then one meeting Source contains the resulting transcript Fragments.

### FR-21 Local dictation

- Given push-to-talk is active, when the owner speaks and releases the control, then local speech-to-text produces text without an outbound request.
- Given Ask or a note is the selected destination, when transcription completes, then the text appears in that destination and remains editable.

### FR-22 Spoken answers

- Given a displayed answer, when the owner selects speak for that answer, then a local voice plays it and the voice's permissive licence is visible in the model catalog.
- Given spoken output is not selected, when an answer arrives, then no speech begins automatically.

### FR-23 Imported media transcription

- Given a supported audio or video Source with processing confirmed, when import runs, then the local meeting transcription pipeline produces timed Fragments.
- Given the same spoken content processed through meeting capture and media import, when outputs are inspected, then both use the same transcription stages and Locator format.

### FR-24 Model manager

- Given the model catalog, when it is opened, then each entry shows size, licence, checksum, hardware fit, and installed or available state.
- Given a model is not installed, when download is requested, then transfer begins only after explicit approval and installation succeeds only after checksum verification.
- Given an installed removable model, when removal is confirmed, then its files are deleted and its catalog state becomes available rather than installed.

### FR-25 Hardware tiers

- Given a CPU-only, 6 GB GPU, or larger reference profile, when first-run selection evaluates the machine, then the matching default model set is proposed.
- Given the detected tier, when the owner reviews the choice, then model sizes and hardware-fit labels are visible before installation.

### FR-26 Install and first run

- Given a clean supported Windows machine, when the installer is run, then the application launches without requiring a compiler, package manager, or other developer tool.
- Given the first launch, when setup runs, then it guides the owner through a hardware-appropriate model choice before model-dependent features are used.

### FR-27 Loopback integrations

- Given the local HTTP API and read-only MCP server are running, when a non-loopback host or an invalid secret connects, then access is denied.
- Given a valid per-launch secret on loopback, when a read operation is requested through either interface, then authorized data is returned and no write operation is exposed by the MCP server.
- Given an application restart, when integration access is retried with the prior secret, then it fails because a new shared secret was issued for the launch.

### FR-28 Backup, restore, and complete export

- Given a populated data folder, when backup and restore complete, then Sources, authoritative Page revisions, review state, settings, and every Page revision (revisions are authoritative per ADR-031) are available in the restored workspace.
- Given the owner requests the complete data export, when it finishes, then the export includes every Source, Fragment, Page with all revisions, Concept, relation, and Evidence row that exists in the workspace.

### FR-29 Language, theme, and accessibility

- Given English, Polish, Italian, or Danish is selected, when any knowledge-base screen opens, then its interface text is presented in that language.
- Given light or dark mode is selected, when the application is reopened, then the selected theme remains active.
- Given keyboard-only or screen-reader use, when the owner imports, reviews, traverses the graph, and reads Pages, then every action and status is operable and announced without pointer input.

### FR-30 Cloud parity and sync

- Given the Azure engine after cloud catch-up, when shared port contract suites run, then the same Source, hierarchy, review, and Page operations supported by local stores pass there.
- Given reviewed local knowledge, when synchronization completes, then Sources, hierarchy links, review states, and approved Pages appear remotely with their identifiers and states preserved.

## Quality attributes and measurement gates

No numerical result is claimed before it is measured on the reference machine or named test environment. Targets below are provisional until the first measurement; published results must identify the dataset, hardware, build, and measurement method.

| Attribute | Measurement | Target or required result | Claim gate |
|---|---|---|---|
| Grounding precision | Run the expanded golden set, targeting at least 150 cases across four languages, and verify every citation against its stored Fragment. | At least 95% verifiable citation precision. | Report the measured numerator, denominator, dataset revision, and build before claiming the percentage. |
| Workspace isolation | Run cross-workspace retrieval, API, graph, Page, cache, and sync security cases. | Zero cross-workspace leaks. | Claim isolation only after all named suites record zero leaks for the build. |
| Deleted-data isolation | Delete seeded Sources and query every read path, including cached answers and stored Page revisions. | Zero deleted-data leaks. | Claim erasure only after the deletion test records absence on every named path. |
| Deletion completeness | Inspect Fragments, indexes, graph Evidence, Pages, and answer cache after deletion. | No record or readable dependent claim remains; prescribed deletion markers may remain. | Publish the checked stores and result rather than an unmeasured completeness statement. |
| Ask latency | Measure end-to-end Ask latency on the reference machine with the defined evaluation workload. | p95 no more than 8 seconds. | Claim the target only with a recorded sample count, workload, build, and p95. |
| Import performance | Time a defined 50-page PDF and one hour of audio on the reference machine. | Record both results in `docs/PERFORMANCE.md`; no threshold is set before measurement. | Do not describe import as fast or quote a number until the benchmark is recorded. |
| Offline operation | Block outbound traffic and run import, retrieval, Ask, transcription, Page, and model-use tests; separately detect new outbound calls in the build. | Core local operation succeeds and the build makes no new outbound network call in local mode. | Claim offline operation only after the blocked-network suite passes and the outbound-call count is measured. |
| Import security | Exercise document prompt injection, path traversal, and zip-bomb cases. | Injected instructions have no effect; unsafe paths and oversized archives are rejected. | Record the fixture set and pass/fail result for the build. |
| Local interface security | Probe the local API and MCP server from non-loopback, through DNS rebinding, and with invalid credentials. | Every prohibited connection is denied. | Claim loopback isolation only after every probe is recorded as denied. |
| Accessibility | Run axe on every new screen and execute keyboard-only import, review, and graph journeys, with screen-reader checks for their states. | No blocking automated violations; all named journeys complete without pointer input. | Publish tested screens, journeys, assistive setup, and results before claiming conformance. |
| Contract compatibility | Run source-neutral graph, answer-cache, Concept, and review contracts on memory, SQLite, and PostgreSQL stores. | All applicable shared contracts pass; local-only cases are explicitly marked until Phase 10. | A migration or parity claim requires recorded green results for all applicable stores. |
| Installation | Run an installer smoke test on a clean supported Windows virtual machine. | Install, first run, model guidance, launch, and removal complete without developer tools. | Claim one-click installation only after the clean-VM result is recorded. |

## Traceability

The ADR column lists decisions that directly constrain the requirement. A dash means no specific ADR exists yet.

| Requirement | Phase | ADR |
|---|---|---|
| **FR-1** | Phase 2 - Documents | [ADR-029](../adr/029-source-model.md) |
| **FR-2** | Phase 2 - Documents | [ADR-007](../adr/007-derived-stores-rebuildable.md), [ADR-029](../adr/029-source-model.md) |
| **FR-3** | Phase 2 - Documents | [ADR-002](../adr/002-event-driven-ingestion.md) |
| **FR-4** | Phase 3 - Hierarchy | [ADR-029](../adr/029-source-model.md), [ADR-030](../adr/030-concept-hierarchy-as-relation.md) |
| **FR-5** | Phase 2 - Documents | [ADR-005](../adr/005-attestation-not-consent.md), [ADR-019](../adr/019-compliance-and-data-governance.md), [ADR-029](../adr/029-source-model.md) |
| **FR-6** | Phase 2 - Documents | [ADR-029](../adr/029-source-model.md) |
| **FR-7** | Phase 3 - Hierarchy | [ADR-030](../adr/030-concept-hierarchy-as-relation.md) |
| **FR-8** | Phase 3 - Hierarchy | [ADR-030](../adr/030-concept-hierarchy-as-relation.md) |
| **FR-9** | Phase 5 - Wiki and health | [ADR-012](../adr/012-graph-architecture.md), [ADR-019](../adr/019-compliance-and-data-governance.md), [ADR-029](../adr/029-source-model.md), [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-10** | Phase 4 - Retrieval | [ADR-003](../adr/003-hybrid-retrieval-graph-acl.md), [ADR-011](../adr/011-retrieval-and-graph-staging.md), [ADR-012](../adr/012-graph-architecture.md) |
| **FR-11** | Phase 4 - Retrieval | [ADR-003](../adr/003-hybrid-retrieval-graph-acl.md), [ADR-011](../adr/011-retrieval-and-graph-staging.md) |
| **FR-12** | Phase 4 - Retrieval | [ADR-003](../adr/003-hybrid-retrieval-graph-acl.md) |
| **FR-13** | Phase 4 - Retrieval | [ADR-029](../adr/029-source-model.md) |
| **FR-14** | Phase 4 - Retrieval | [ADR-012](../adr/012-graph-architecture.md), [ADR-029](../adr/029-source-model.md) |
| **FR-15** | Phase 5 - Wiki and health | [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-16** | Phase 5 - Wiki and health | [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-17** | Phase 5 - Wiki and health | [ADR-012](../adr/012-graph-architecture.md) |
| **FR-18** | Phase 5 - Wiki and health | [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-19** | Phase 6 - UI | [ADR-025](../adr/025-review-inbox.md), [ADR-030](../adr/030-concept-hierarchy-as-relation.md), [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-20** | Phase 7 - Voice and models | [ADR-005](../adr/005-attestation-not-consent.md), [ADR-020](../adr/020-speech-in-stages.md), [ADR-028](../adr/028-voice-gateway.md), [ADR-029](../adr/029-source-model.md) |
| **FR-21** | Phase 7 - Voice and models | [ADR-028](../adr/028-voice-gateway.md) |
| **FR-22** | Phase 7 - Voice and models | [ADR-028](../adr/028-voice-gateway.md) |
| **FR-23** | Phase 2 - Documents | [ADR-020](../adr/020-speech-in-stages.md), [ADR-029](../adr/029-source-model.md) |
| **FR-24** | Phase 7 - Voice and models | - |
| **FR-25** | Phase 7 - Voice and models | - |
| **FR-26** | Phase 9 - Packaging | [ADR-014](../adr/014-desktop-app-and-local-engine.md) |
| **FR-27** | Phase 8 - Integration | [ADR-014](../adr/014-desktop-app-and-local-engine.md) |
| **FR-28** | Phase 8 - Integration | [ADR-017](../adr/017-resilience-and-disaster-recovery.md), [ADR-019](../adr/019-compliance-and-data-governance.md), [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
| **FR-29** | Phase 6 - UI | [ADR-008](../adr/008-multilingual-strategy.md), [ADR-009](../adr/009-expo-router-i18next-tokens.md), [ADR-027](../adr/027-nextjs-web-interface.md) |
| **FR-30** | Phase 10 - Cloud catch-up | [ADR-010](../adr/010-azure-target-and-iac.md), [ADR-015](../adr/015-sync-local-to-azure.md), [ADR-029](../adr/029-source-model.md), [ADR-031](../adr/031-wiki-pages-and-human-edits.md) |
