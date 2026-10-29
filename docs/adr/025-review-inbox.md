# ADR-025: What the system proposes waits in an inbox until a person decides

Status: accepted. Implemented and tested end to end on the memory, SQLite and PostgreSQL stores (one contract suite), through the API, and in the app (data layer in all four languages, the proposal card and the settings controls rendered in tests). Not looked at on a screen: no visual check was run.

## Context
ADR-004 says a decision is a proposal until a person approves it, and the graph has always stored the status of every relation (`proposed`, `approved`, `rejected`) and an endpoint to change it. What was missing was the place where a person *sees* what is waiting: the list of proposals, in words, with enough context to decide, and an answer that is one tap. Without it the rule is true in the database and invisible to the people it protects.

## Decision
**One list, from the store.** `GET /v1/graph/reviews?limit=` returns the relations the model proposed and nobody has decided, newest first, as a normal subgraph: the relations with their evidence, and the entities at their ends. `truncated` says there are more than `limit`. Every store implements `pending_review`; one contract suite runs on the memory, SQLite and PostgreSQL stores (kinds, ends, evidence, a decision taking an item out, the limit, isolation between owners and workspaces, erasure of the meeting). What counts as a proposal is what the graph already says: relations the model inferred (about, proposed by, assigned to, depends on, supersedes, contradicts) and the link from a decision to the meeting that decided it. Plain mentions are approved on creation and never appear.

**Deciding is an audited act.** `PATCH /v1/graph/relations/{id}` (unchanged) now writes an audit event `relation_reviewed` with the relation id and the decision, ids only, so who-decided-what is part of the evidence trail that leaves the database for the immutable archive (ADR-022).

**The app.**
- A screen "To review" (`/review`) in all four languages: each proposal as a sentence ("Keep the flow synchronous was proposed by Marco"), the types of its two ends, the system's confidence, and **Approve** and **Reject**. A card shows its answer being saved and can not be answered twice; a failed save says so and leaves the card. "Open source meeting" goes to the meeting the proposal came from. An empty inbox says nothing is waiting.
- The graph screen shows how many proposals are waiting, with a button to the inbox.
- In settings, for the engine on this computer, a "Search index" section checks that search and the graph match the archive and repairs them (ADR-021), naming each kind of difference in words.
- The sample data (mock) behaves like the real thing: proposals wait, a decision removes the item and an approved relation shows as approved in the graph; a rejected one disappears.

## Alternatives considered
- **Show proposals only inside each meeting.** The decision is about one meeting, but the list of everything waiting is what keeps an inbox from being forgotten.
- **Approve whole meetings in one tap.** Faster and exactly the habit the rule exists to prevent: the model is wrong sometimes and a bulk approval hides which times.
- **A notification when proposals arrive.** Worth doing once there is a notification channel; the count on the graph screen is the minimum that works today.

## Consequences
- The inbox is limited to 100 per request in the app and says when there are more; deciding makes room for the rest.
- The confidence number is the model's own and is shown as a hint, not as a decision aid: a high number is not a reason to approve.
- No accessibility audit was done beyond using real buttons with labels, hints and a live region for the list; a screen reader pass is still to do.
