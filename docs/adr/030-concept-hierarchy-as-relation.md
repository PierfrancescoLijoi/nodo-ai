# ADR-030: Concept hierarchy as a relation, not a column

Status: accepted (extends ADR-012; builds on ADR-029; the PostgreSQL schema of ADR-012 is untouched)

Implements the knowledge-base design in `docs/superpowers/specs/2026-11-12-knowledge-base-design.md` (sections 5 and 7). Phase 1 of its work breakdown adds the vocabulary; Phase 3 adds the enforcement and the features.

## Context
Concepts (projects, systems, topics) need a hierarchy: "Payments" sits under "Platform", a topic belongs to two projects. A knowledge base with hundreds of sources is hard to browse as a flat list, and retrieval and wiki pages (later phases) want to walk up and down the tree.

Two different kinds of hierarchy exist and they are not the same thing. The structure of a source (source, section, fragment) and the grouping of sources into collections is fact: the system reads it from the file or the user builds it. The hierarchy between concepts is interpretation: a model suggests that one topic belongs under another, and it can be wrong. The graph already has a mechanism for interpretation, namely typed relations with a review state, confidence, validity time and evidence (ADR-012).

## Decision
**A relation, not a column.** The hierarchy between concepts is stored as the relation kind `CHILD_OF`, directed from child to parent. There is no `parent_id` column on `entities`.

**Several parents are allowed.** A concept may have any number of `CHILD_OF` edges. The hierarchy is a directed acyclic graph, not a tree.

**Endpoints.** `CHILD_OF` joins two concepts of the thing types. In `ENDPOINTS` (`services/api/src/nodo_api/domain/graph.py`) it is `CHILD_OF: (_THINGS, _THINGS)`, where `_THINGS` is project, system and topic. People, decisions and actions have no hierarchy of their own; a decision is placed through its `ABOUT` relation. Source nodes (`MEETING`, `SOURCE`, ADR-029) are not endpoints.

**Review follows the origin.** A `CHILD_OF` link that a model infers is a `proposed` relation with confidence and evidence, and appears in the review inbox like any other proposal. A link that a person creates or approves is `approved`. `CHILD_OF` is not added to `AUTO_APPROVED`, which stays limited to facts of record that the system derives by rule (`MENTIONED_IN`).

**Structure is not in the graph.** Source to section to fragment, and collection nesting, stay in their own tables (ADR-029, spec section 7). They are derived by rule or built by the user, so they are authoritative at once and need no review. Only the inferred concept links go through the proposal flow.

**Acyclic on write.** Before a `CHILD_OF` edge is stored, the writer checks that the parent is not already a descendant of the child. The check is a recursive query with a depth cap of 6. An edge that would close a cycle, or that sits deeper than the cap allows, is rejected rather than repaired, in line with `validate_extraction` (ADR-012: anything else is dropped, not repaired). This cap is for hierarchy walks and is separate from the depth-2 limit that ADR-012 sets for general graph reads.

**Phase split.** Phase 1 only adds the vocabulary: the `CHILD_OF` value in `RelationKind`, its `ENDPOINTS` entry, the widened `CHECK` in the SQLite schema, and a contract case showing that the relation can be stored and traversed on the memory and SQLite stores. Phase 1 does not check for cycles and does not cap depth, and nothing in the extraction prompt asks for `CHILD_OF` yet, so no such edge appears on its own. Phase 3 adds the acyclic check, the depth cap, collections, the outline API, hierarchy proposals and parent suggestions for concepts.

## Alternatives rejected
- **A `parent_id` column on `entities`.** It allows a single parent, so a topic that belongs to two projects cannot be expressed. It also bypasses everything the relation model gives for free: a link written to the column has no review state, no confidence, no evidence quote to justify it and no validity time, so an approved link cannot be told from a guess and history cannot be replayed with `as_of`. Erasure would need new code, whereas a relation without evidence is already swept (ADR-012).
- **Auto-approving inferred hierarchy.** A wrong parent distorts every outline and every scoped question below it. Inferred links wait for a person, as `ABOUT` and `DEPENDS_ON` do today.
- **Putting structure in the relation as well.** Storing every section and fragment as `CHILD_OF` edges would multiply rows by orders of magnitude and slow the bounded graph reads for no gain, since that structure is already exact.

## Consequences
- Hierarchy reuses the review inbox, confidence, validity time, evidence and erasure sweeps unchanged. When the last evidence for a `CHILD_OF` edge is deleted, the edge goes with it.
- The cost is an acyclicity check on every write (Phase 3) and a recursive query with a depth cap of 6, where a column would have been a plain lookup. The cap bounds the cost.
- Several parents mean a concept can appear under more than one place in the outline; the outline must say so and not count it twice.
- Between Phase 1 and Phase 3 the relation exists but is unguarded. Only the contract test writes it, and the extraction prompt does not produce it; Phase 3 must add the guard before anything else can create these edges.
- `relations.kind` has a `CHECK` list in both SQLite and PostgreSQL. SQLite must widen it, which takes a table rebuild (the Phase 1 graph vocabulary migration). The PostgreSQL migrations are untouched until Phase 10, so the PostgreSQL store rejects `CHILD_OF` until then, in line with the local-only rule of ADR-029. The new contract case runs on the memory and SQLite stores first.
