# ADR-026: A question already answered is answered again only while that answer is still true

Status: accepted. Implemented and tested on the memory, SQLite and PostgreSQL stores (one contract suite, real pgvector), through the query service with every kind of change that must drop it, and calibrated on the real local embedding model. Not measured on the Azure models: the Azure threshold is a conservative guess until `admin calibrate-answer-cache` has run there.

## Context
Every question ran the whole path again: search, graph walk, the language model, validation. Two people asking the same thing, or one person asking it twice, paid the model each time (seconds locally, tokens in the cloud). A cache is the obvious answer and the dangerous one: a repeated answer that is wrong, stale, or built from a meeting that was since deleted is worse than a slow one, and the cache stores quotes from transcripts, so it is personal data with its own erasure obligations.

## Decision
**Scope.** An entry belongs to one workspace and one owner and serves that owner's questions only. Tables `answer_cache` and `answer_cache_epoch` (migration 0011; SQLite 0006) carry row level security like every table; every query also filters by owner. Nothing is shared between people, so there is no cross-user leak to reason about.

**What makes two questions "the same".** All of these must hold:
- the nearest stored question has cosine similarity at or above a threshold (below);
- same interface language, because the answer is written in it;
- same search filters;
- same numbers, same names (capitalised words after the first) and same negation. This is not decoration: measured on the local model, "what did we decide" and "what did we **not** decide" score 0.97, above most true paraphrases (0.93 to 0.98). A similarity threshold alone can never separate them, so these are compared as words;
- same `version`: chat model, embedding model and a fingerprint of the answer prompt and schema. Changing any of them makes every old entry unreachable, with nothing to remember to bump.

**What is stored.** Only complete answers: not the sources-only fallback (the model was down), not "I can not answer" (a meeting recorded tomorrow may answer it), and never one without citations.

**Never stale.** Three independent defences:
1. *Epoch.* Every owner has a counter. Anything that changes what a question would find or say raises it and deletes the owner's entries: a transcript correction, a deletion, a decision on a proposal, a new analysis (including re-analysis after naming a speaker), a copy from the desktop, an index repair. A question reads the epoch before it starts and its answer is stored only if the epoch is unchanged, in the statement that writes. A meeting deleted while a question was being answered can not end up quoted from the cache. Invalidation raises on failure, so the operation that changed the data is repeated (deletion already is).
2. *Re-validation on every hit.* The cited quotes must still exist, word for word, in a meeting the person still owns, the same check a fresh answer passes. If a hit fails it, the cache was not told about something: the owner's entries are dropped and the question is answered normally (counted as `stale`).
3. *Lifetime.* Entries older than `NODO_ANSWER_CACHE_TTL_SECONDS` (24 h) are not served and are removed by the next write; at most 200 per owner are kept.

**Never costs an answer.** Embedding the question, reading or writing the cache failing are logged by error type and the question is answered as if there were no cache. The question's embedding is shared with the entity search, so a miss costs no second call to the model.

**Threshold.** The embedding model decides it. `python -m nodo_api.admin calibrate-answer-cache` measures 16 pairs of the same question in other words and 17 look-alikes (another person, another month, the opposite, a denial) in four languages, ignores the look-alikes the word comparison already separates, and suggests the value that keeps the rest out. Local E5, measured: repeats 0.934 to 0.981 (median 0.957), look-alikes the words do not separate up to 0.951, suggested 0.956, default **0.96**: about half of the repeats are caught and no look-alike in the set. Azure default **0.97**, a guess (`text-embedding-3` spreads scores differently); calibrate it in the first deployment (FIRST-DEPLOY step 8). `NODO_ANSWER_CACHE_MIN_SIMILARITY` overrides; `NODO_ANSWER_CACHE_ENABLED=false` switches the cache off.

**Observability.** Counter `nodo.query.cache` by outcome: `hit`, `miss`, `skip` (the question could not use the cache), `stale`, `invalidated`; the query duration histogram carries the outcome too.

## Alternatives considered
- **Exact question text only.** Safe and nearly useless: people re-ask in other words.
- **A lower threshold.** Rejected by the measurement: look-alikes reach 0.95 and a denial 0.97.
- **A classifier or the model itself deciding "same question".** Costs a model call, which is what the cache saves, and is a second place to be wrong.
- **Expiry only.** Serves a deleted meeting's words for up to a day; unacceptable for erasure.
- **A shared cache across people.** Larger hit rate; each person's answer depends on their own meetings, so it would need a proof of equal access for every hit. Not worth the risk.

## Consequences
- Erasure: deleting a meeting deletes the owner's cache in the same operation; the residual windows are the same as for the rest of the database (backups, ADR-019). Export (GDPR art. 15) does not include the cache: it is derived data and rebuildable.
- A semantic flip the word comparison does not see ("proposed" against "opposed", 0.95) is held back only by the threshold. The set is small; a wrong repeat is still possible and is the reason the default is high and the switch exists.
- "Last week" asked on two different days is the same question to the cache; the entry's 24 hours bound the error, and the answer model does not know the date either.
- A write costs one extra statement per change to the owner's data. A change nobody asks about later costs nothing more.
- A change by the maintenance identity needs two grants, in the migration: update the epoch and delete entries, with read access to the two columns it filters on and no access to the answers.
