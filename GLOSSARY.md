# NODO

NODO is a local-first knowledge base. People put sources into a workspace, and the system derives a cited graph and wiki from them that a person reviews.

## Language

### Ownership

**Workspace**:
The boundary that holds one owner's sources, graph and pages. Nothing crosses from one workspace to another.
_Avoid_: Tenant, account, project

**Owner**:
The person who controls a workspace and decides what is kept, approved or deleted.
_Avoid_: User, admin

### Material

**Source**:
One thing put into the knowledge base, with a kind such as meeting, document, deck, web, note or audio. It is the unit of the archive.
_Avoid_: Document (as the general word), file, item, input

**Meeting**:
A Source of kind meeting: a recorded conversation that carries an attestation and speaker names.
_Avoid_: Call, session, recording

**Attestation**:
The owner's recorded statement, made before capture, that everyone involved has been told about the recording.
_Avoid_: Consent form, waiver

**Section**:
A titled part of a source, such as a chapter, heading, slide or agenda topic. Sections nest.
_Avoid_: Chapter, part, block

**Fragment**:
The smallest citable piece of text of a source, such as a paragraph, a table, a slide's text or a transcript phrase.
_Avoid_: Chunk, segment (except for a transcript phrase), snippet, passage

**Locator**:
The position of a fragment inside its source: a time range, page, slide or character span.
_Avoid_: Offset, pointer, anchor

### Knowledge

**Concept**:
A graph entity with meaning, such as a person, project, system, topic, decision or action. A concept may have several parents.
_Avoid_: Entity, node, tag

**Collection**:
A folder tree managed by the owner that groups sources. It is structural and never inferred.
_Avoid_: Folder, library, category

**Page**:
A compiled wiki page about a concept, a source or a question. It is always derived and always cited.
_Avoid_: Article, note, entry

### Trust

**Proposal**:
Anything the system inferred, such as a relation, decision, hierarchy link or page, that a person has not yet approved.
_Avoid_: Suggestion, draft, candidate

**Evidence**:
A fragment, with a quote, that supports a relation or a claim on a page.
_Avoid_: Citation (the link a reader follows), proof, reference
