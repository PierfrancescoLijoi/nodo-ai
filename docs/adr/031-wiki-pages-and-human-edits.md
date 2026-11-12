# ADR-031: Wiki pages are cited proposals, and human edits and source deletion both win

Status: accepted (builds on ADR-029 and ADR-012; makes one deliberate exception to ADR-007)

Implements the knowledge-base design in `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (sections 7 and 16). Pages are Phase 5 work. Phase 1 adds nothing for pages: no tables, no ports, no domain types. This ADR fixes the rules now because they decide the id scheme and the erasure design that Phases 1 to 4 build.

## Context
The knowledge base compiles wiki pages (about a concept, a source or a saved answer) from the archive. A page is prose written by a model, so it can be wrong in the same ways an answer can: a claim with no support, a quote that is not in the source. NODO already refuses that for answers (ADR-012 and the evidence validator in `services/api/src/nodo_api/services/query.py`: a citation counts only if its quote matches the stored text of the fragment it names). Pages need the same guarantee, claim by claim.

Two further facts make pages different from every other derived store in NODO.

First, people edit them. A person who corrects a page has said something the model cannot reproduce. If the next regeneration overwrote the edit, the product would punish the reader for using it.

Second, they hold text copied from sources. Deleting a source must erase what NODO learned from it (FR-9, ADR-019). A page body and its stored revisions are such text. If revisions were kept intact for the sake of history, a deleted document would live on inside the wiki.

Until now every derived store (search index, graph projections, section summaries, cached answers) can be dropped and rebuilt from the sources (ADR-007). Pages cannot, once a person has edited one.

## Decision
**Pages are proposals.** A page is derived: a model compiles it from fragments and it starts as a proposal that a person approves, edits or rejects, in the same review inbox as relations and hierarchy links. It never becomes fact by itself.

**Every claim is cited and validated.** The body is split into claims (`page_claims`). Each claim has one or more citations (`page_citations`) holding `source_id`, `fragment_id` and the quoted text. The same evidence validator that guards answers and graph evidence checks them: the fragment must exist in the caller's workspace and the quote must match the stored fragment text (for new source kinds, ADR-029 and the spec: the quote must be a substring of the fragment). A citation that fails is dropped. A claim with some failing citations is kept only if its remaining citations still validate its text; otherwise the claim is dropped from the proposal, not repaired. A page with no valid claim is not proposed.

**Human revisions are authoritative.** `page_revisions(page_id, rev, body_md, author, at, provenance)` keeps every revision. A revision whose author is a person is never overwritten, merged away or replaced by a later model run, except by redaction when a source is deleted (see below). The current body is the latest revision. A revision created by approving a model proposal has the approving person as `author` and records the proposal origin (`model_version`, `prompt_version`) in `provenance`, so authorship and machine origin are both visible.

**Claims and citations are recorded per revision.** `page_claims` and `page_citations` describe only the current body, and an old revision is plain `body_md`, so erasure could not find the claims inside it. Phase 5 therefore stores, for every revision, its claim spans and their citations: `page_revision_claims(page_id, rev, ordinal, span_start, span_end, text_hash)` and the matching per-revision citations (`source_id`, `fragment_id`, `quote`). A claim or passage that a person adds or rewrites is cited by the editor, or it stays uncited. Uncited text is the own words of the person: source erasure does not redact it, and lint reports it as an uncited claim.

**Regeneration is a new proposal against the human revision.** When sources change, or a person asks for a refresh, the compiler reads the latest human revision as input and produces a new proposal next to it. Approving the proposal creates a new revision; rejecting it leaves the human text as it is.

**Superseded fragments make a page stale, not deleted.** A new source version keeps the id of any fragment whose section path and text hash are unchanged. Changed or removed fragments stay stored as superseded (spec section 7). A page that cites a superseded fragment is marked `stale` and keeps its text and its citations, which still show the version they cite. It is regenerated as a proposal against the human revision. Nothing is removed.

**Deletion of a source wins over everything.** When a source is deleted, all its versions, fragments, index rows, graph evidence, orphaned concepts and cached answers are erased (ADR-029, ADR-012), and pages follow this rule:
- A claim that cited only erased fragments is removed from the current body.
- A claim that cited an erased fragment and also a surviving one is kept only if its text still validates against the surviving fragments alone (the same quote and substring rule). Otherwise it is removed like an only-erased claim. This refines the literal rule of the spec, which keeps such a claim and drops only the erased citation. That rule is not safe: the claim can state facts that exist only in the erased source, and a surviving citation next to them does not support them. The refined rule fails closed.
- In every case the page is marked `stale`, so it regenerates as a proposal against the remaining sources.
- The same claim is redacted in every stored revision, human revisions included, using the per-revision claim spans and citations. A revision is not deleted; the claim text in it is replaced by a visible marker, "removed: source deleted". Revision numbers, authors and times stay.
- Fail closed on mapping: if a span of any revision cannot be mapped (a missing mapping, or the text no longer matches its `text_hash`), the whole body of that revision is redacted and the marker is left in its place.
- Uncited human-written text depends on no source and is kept. A passage a person cited to an erased fragment is a claim of that source and is redacted.
- Pending and rejected page proposals that cite erased fragments are deleted. A source page left with only markers is deleted.
- Backups and exports taken before the erasure keep the erased text. This is a known limit, of the same kind as the residual windows that ADR-019 states for blob soft delete and database backups: erasure is not instantaneous or total for copies outside the store, and the notice to the user says so.

**The one exception to rebuildable projections.** ADR-007 says derived data can be dropped and rebuilt from the source of truth. Pages with a human revision cannot: the revision is source of truth in its own right. This is the single, deliberate exception. Pages that have only model-authored revisions remain rebuildable. Every backup, export and migration that touches `page_revisions` must treat it as authoritative data, not as a cache.

**Local-only until Phase 10.** Pages follow the rule of ADR-029 for documents and hierarchy: they exist on the memory and SQLite stores first, and the Azure engine reports them as unavailable. PostgreSQL tables, migrations and sync for pages arrive in Phase 10.

**Phase split.** Phase 1 adds nothing for pages. Phase 5 adds the tables (`pages`, `page_claims`, `page_citations`, `page_revisions`, `page_revision_claims` and its per-revision citations), the page compiler, the claim validator, revisions, staleness, redaction on source deletion and the lint rules (uncited claims, dangling evidence, stale pages). The per-revision claim records and the redaction step are required before any page is stored.

## Alternatives rejected
- **Regenerate pages freely, treating them as a cache.** It is simple, and fits ADR-007 with no exception. It destroys human edits on the next run, so people would stop correcting pages, and the pages would stay as wrong as the model.
- **Keep revisions intact on deletion, for history.** It keeps a complete audit trail, but a deleted source would stay readable in old revisions, in exports and in backups. That breaks FR-9 and the erasure promise of ADR-019.
- **Delete the whole page when any cited source is deleted.** It is safe but throws away every claim that still stands on other sources, and it deletes human work that does not depend on the erased source.
- **Delete stale pages when a fragment is superseded.** A superseded fragment is not erased, so the page still explains where its claims came from. Deleting would lose the human revision for no privacy gain. Stale means "regenerate and review", not "gone".
- **Pages without per-claim citations (one citation list per page).** It cannot say which sentence an erased source supported, so erasure would have to remove the whole page or none of it, and lint could not find uncited claims.
- **Let a model repair claims whose quotes do not match.** The same rule as ADR-012 applies: anything that cannot be verified is dropped, not repaired.

## Consequences
- Pages reuse the evidence validator, so a quote check that is tightened for answers is tightened for pages. One rule, not two.
- Erasure gains a step: after a source is erased, claims that lose a citation are re-validated against the surviving fragments, removed from the body if they fail, and redacted in all revisions. This must run in the same operation as the erasure, and its tests must read every stored revision, not only the current body.
- `page_revisions` is authoritative data. Backup and export must include it; it cannot be skipped as derived. This is the cost of the exception to ADR-007.
- Redaction rewrites stored revisions, so history is not append-only for erased content. The marker keeps the structure visible, which is the point: a reader can see that something was removed, not what.
- Deciding which human-written passages depend on an erased source is explicit, not guessed: a passage depends on a source only if the editor cited it. Uncited text is kept, and lint reports it, so the cost of a wrong or missing citation is visible. Everything else fails closed: a claim that no longer validates on surviving fragments, or a revision whose spans cannot be mapped, is redacted. A false redaction costs the person a sentence they can write again; a missed one leaks erased content.
- Per-revision claim records add rows for every revision. That is the price of redacting old revisions precisely, and it is bounded by page size.
- A stale page costs a regeneration and a review, not data. Superseded fragments must stay stored for as long as any page or approved relation cites them, which is already the rule of the version model (spec section 7).
- Between Phase 1 and Phase 5 nothing creates or reads a page, so there is no unguarded path. Phase 5 must not store a page before the claim validator and the redaction step both exist.
- Cloud parity is deferred: until Phase 10 a workspace on Azure has no pages.
