# NODO Product

**Version:** v0.2  
**Status:** Draft

## Vision

NODO is a local personal knowledge base that turns material the owner chooses to keep into connected, reviewable knowledge. A meeting is one kind of Source among documents, decks, saved web material, notes, and audio. Each Source is organized into citable Fragments, connected through Concepts, and available for questions that either return supported answers or decline to answer.

The product is designed to run entirely on the owner's Windows computer. It brings the archive, graph, review workflow, and cited wiki together without making the local experience depend on a cloud service.

## Personas

### The knowledge-intensive professional

The primary owner works across several projects and needs to recover decisions, context, and relationships without relying on memory. They value the original Source and its exact Evidence more than a fluent but unsupported summary.

### The privacy-conscious researcher

This owner gathers presentations, documents, notes, recordings, and saved web material that may be sensitive. They want one searchable workspace, explicit control over downloads and retention, and a readable account of what the system inferred.

Both personas may be non-developers. Installation and everyday operation must not require engineering knowledge.

## A typical day

In the morning, the owner records an announced and attested meeting. Its transcript becomes a meeting Source with timed Fragments. Later, they import a project deck, which becomes another Source with its slide structure and Locators preserved.

The owner asks a question across the meeting and deck. NODO retrieves authorized Evidence from both and returns a cited answer, or `UNANSWERABLE` if the stored material is insufficient. The owner then opens Review, examines a proposed relationship and a proposed Concept hierarchy link, and approves or rejects each one.

Finally, the owner reviews a proposed Page that brings the supported knowledge together. Each generated claim links back to Evidence. Approval creates a human-authorized wiki revision; later regeneration must not overwrite that revision.

## Product principles

1. **Support every claim or decline to answer.** Answers and generated Page claims must trace to text in a stored Fragment.
2. **Filter before searching.** Material outside the owner's authorized workspace must never enter retrieval or model context.
3. **Make recording unmistakable.** Starting capture is intentional, requires the meeting attestation, and stays visible while active.
4. **Treat inference as a Proposal.** People approve or reject inferred relations, decisions, hierarchy links, and Pages.
5. **Preserve the archive as the foundation.** Search indexes and graph projections can be rebuilt from authoritative records; approved or edited Page revisions remain authoritative in their own right.
6. **Acknowledge only durable work.** Audio and jobs are not reported as accepted until their required state has been stored.
7. **Put explicit limits on work.** Inputs, concurrency, retries, traversal, buffers, and session duration are bounded.
8. **Keep behavior consistent across engines.** Local and cloud implementations share a product contract, while knowledge-base capabilities remain local-only until their planned cloud catch-up.
9. **Make the accessible representation primary.** Lists, trees, keyboard operation, and screen-reader semantics come before optional visual maps.
10. **State verification honestly.** Plans, simulations, and local tests are not presented as evidence of a live production deployment.

## Screens

The planned navigation is **Home · Library · Capture · Ask · Graph · Wiki · Review · Health · Models · Settings**.

### Home

A Today view brings together live and recent Sources, the Review count, Health findings, quick Ask, and shortcuts for capture and import.

### Library

Library replaces the meetings-only list. It combines the Collection tree with Sources filtered by kind, date, or language. Import state and progress appear in an activity panel, and files can enter through drag-and-drop.

### Source detail

The viewer adapts to the Source kind: speaker-aware transcript, document outline and text, or slide list. An outline panel shows the Section tree, while a connections panel shows Concepts, related Sources, and Pages. A citation opens the relevant Locator.

### Capture

Capture retains the visible recording notice and attestation. It supports a live meeting transcript, Ask while recording, and an entry point for dictation.

### Ask

The owner chooses a scope, may restrict retrieval to Sources, and receives answers with citations appropriate to each Source kind. An individual answer may be spoken locally or saved as a Page Proposal.

### Graph

An accessible hierarchy tree or list is the primary view, with the visual map as a secondary view. Filters cover kind, Collection, and time; community and bridge-Concept insights form an additional layer.

### Wiki

Wiki provides a Page tree, a reader with claim-level footnotes, a Proposal diff with approve, edit, and reject actions, and one-way vault export.

### Review

One inbox contains relation, decision, hierarchy-link, and Page Proposals for human review.

### Health

Health lists knowledge-lint findings and graph insights. Each finding either opens the affected item or offers a fix that creates a Proposal.

### Models

The model catalog shows size, licence, hardware fit, installation state, removal controls, and disk use. Downloads require the owner's approval.

### Settings

Settings continues to hold product preferences and controls governed by the existing design system, including language, theme, export, and local capabilities.

## Out of scope for v0.2

- Voice cloning, voice design, dubbing, voiceprints, and biometric speaker recognition.
- A browser extension or web clipper, calendar integration, and Teams integration.
- Multi-owner or multi-user collaboration.
- An autonomous agent with shell access or file-writing capabilities.
- Two-way synchronization with the exported Markdown vault; export is one-way in this phase.
- New mobile application work; the existing mobile client remains on its meetings view.
- Live Azure or AWS deployment, new infrastructure definitions, or new cloud adapters beyond preserving existing contracts.
- Network fetching for web Sources; this phase accepts saved files or pasted text.
- OCR for scanned PDFs and images; they are identified as having no text layer and surfaced in Health.

## References

- [Knowledge base design](../superpowers/specs/2026-11-12-knowledge-base-design.md)
- [Knowledge base requirements](REQUIREMENTS.md)
- [Product glossary](../../GLOSSARY.md)
- [ADR-029: Source model](../adr/029-source-model.md)
- [ADR-030: Concept hierarchy as a relation](../adr/030-concept-hierarchy-as-relation.md)
- [ADR-031: Wiki Pages and human edits](../adr/031-wiki-pages-and-human-edits.md)
