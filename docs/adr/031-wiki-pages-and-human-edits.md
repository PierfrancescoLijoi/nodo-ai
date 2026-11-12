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

**Every claim is cited and validated.** The body is split into claims (`page_claims`). Each claim has one or more citations (`page_citations`) holding `source_id`, `fragment_id` and the quoted text. The same evidence validator that guards answers and graph evidence checks them: the fragment must exist in the caller's workspace and the quote must match the stored fragment text (for new source kinds, ADR-029 and the spec: the quote must be a substring of the fragment). A claim whose citations fail is dropped from the proposal, not repaired. A page with no valid claim is not proposed.

**Human revisions are authoritative.** `page_revisions(page_id, rev, body_md, author, at)` keeps every revision. A revision whose author is a person is never overwritten, merged away or replaced by a later model run. The current body is the latest revision.

**Regeneration is a new proposal against the human revision.** When sources change, or a person asks for a refresh, the compiler reads the latest human revision as input and produces a new proposal next to it. Approving the proposal creates a new revision; rejecting it leaves the human text as it is.

**Superseded fragments make a page stale, not deleted.** A new source version keeps the id of any fragment whose section path and text hash are unchanged. Changed or removed fragments stay stored as superseded (spec section 7). A page that cites a superseded fragment is marked `stale` and keeps its text and its citations, which still show the version they cite. It is regenerated as a proposal against the human revision. Nothing is removed.

**Deletion of a source wins over everything.** When a source is deleted, all its versions, fragments, index rows, graph evidence, orphaned concepts and cached answers are erased (ADR-029, ADR-012), and pages follow this rule:
- A claim that cited only erased fragments is removed from the current body.
- The same claim is redacted in every stored revision, human revisions included. A revision is not deleted; the claim text in it is replaced by a visible marker, "removed: source deleted". Revision numbers, authors and times stay.
- A claim that also cites a fragment of a source that still exists is kept, and only the citation to the erased fragment is removed.
- Human-written text is kept only where it does not depend on the erased source. A sentence a person wrote or edited that is supported only by erased fragments is treated as a claim of that source and redacted.
**The one exception to rebuildable projections.** ADR-007 says derived data can be dropped and rebuilt from the source of truth. Pages with a human revision cannot: the revision is source of truth in its own right. This is the single, deliberate exception. Pages that have only model-authored revisions remain rebuildable. Every backup, export and migration that touches `page_revisions` must treat it as authoritative data, not as a cache.

**Local-only until Phase 10.** Pages follow the rule of ADR-029 for documents and hierarchy: they exist on the memory and SQLite stores first, and the Azure engine reports them as unavailable. PostgreSQL tables, migrations and sync for pages arrive in Phase 10.

**Phase split.** Phase 1 adds nothing for pages. Phase 5 adds the tables (`pages`, `page_claims`, `page_citations`, `page_revisions`), the page compiler, the claim validator, revisions, staleness, redaction on source deletion and the lint rules (uncited claims, dangling evidence, stale pages).

## Alternatives rejected
- **Regenerate pages freely, treating them as a cache.** It is simple, and fits ADR-007 with no exception. It destroys human edits on the next run, so people would stop correcting pages, and the pages would stay as wrong as the model.
- **Keep revisions intact on deletion, for history.** It keeps a complete audit trail, but a deleted source would stay readable in old revisions, in exports and in backups. That breaks FR-9 and the erasure promise of ADR-019.
- **Delete the whole page when any cited source is deleted.** It is safe but throws away every claim that still stands on other sources, and it deletes human work that does not depend on the erased source.
- **Delete stale pages when a fragment is superseded.** A superseded fragment is not erased, so the page still explains where its claims came from. Deleting would lose the human revision for no privacy gain. Stale means "regenerate and review", not "gone".
- **Pages without per-claim citations (one citation list per page).** It cannot say which sentence an erased source supported, so erasure would have to remove the whole page or none of it, and lint could not find uncited claims.
- **Let a model repair claims whose quotes do not match.** The same rule as ADR-012 applies: anything that cannot be verified is dropped, not repaired.

## Consequences
- Pages reuse the evidence validator, so a quote check that is tightened for answers is tightened for pages. One rule, not two.
- Erasure gains a step: after a source is erased, claims that lose their last citation are removed from the body and redacted in all revisions. This must run in the same operation as the erasure, and its test must read the stored revisions, not only the current body.
- `page_revisions` is authoritative data. Backup and export must include it; it cannot be skipped as derived. This is the cost of the exception to ADR-007.
- Redaction rewrites stored revisions, so history is not append-only for erased content. The marker keeps the structure visible, which is the point: a reader can see that something was removed, not what.
- Deciding which human-written sentences depend on an erased source is a judgment call. The rule is conservative: text tied to citations that are all erased is redacted. A false redaction costs the person a sentence they can write again; a missed one leaks erased content.
- A stale page costs a regeneration and a review, not data. Superseded fragments must stay stored for as long as any page or approved relation cites them, which is already the rule of the version model (spec section 7).
- Between Phase 1 and Phase 5 nothing creates or reads a page, so there is no unguarded path. Phase 5 must not store a page before the claim validator and the redaction step both exist.
- Cloud parity is deferred: until Phase 10 a workspace on Azure has no pages.
