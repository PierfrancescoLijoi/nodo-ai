# ADR-024: Graph entities are found by meaning as well as by words

Status: accepted. Implemented and tested on the memory, SQLite and PostgreSQL stores with one shared contract suite (PostgreSQL with the real `pgvector`), and measured on the real local embedding model. The Azure embedding model is the one thing not measured: `admin calibrate-entities` does it on the first connection.

## Context
A question starts its walk over the graph at the entities it names: the words of the question are matched against entity labels (trigram index in PostgreSQL, ADR-012). That fails exactly where the product promises most. Entities are named in the language of the meeting that produced them ("Ingestion", "Keep the flow synchronous"); the person asks in theirs ("Cosa abbiamo deciso sull'inserimento dei dati?"). The search over the transcripts already crosses languages (vectors); the graph did not.

## Decision
**One vector per entity label.** When a meeting's extraction is stored, the entity labels that have no vector yet are embedded (in batches of 64, at most 1 280 per call) and stored next to the entity: a table `entity_vectors` in PostgreSQL (migration 0010, which also creates the `vector` extension; row level security like every table) and in SQLite (float32 bytes). A foreign key removes a vector with its entity, so erasing a meeting leaves nothing behind. The column has no fixed size: if the embedding model changes, entities whose vector has another size are listed as missing and re-embedded.

**Semantic seeds join lexical seeds.** A question is embedded (on the query side of the model where it has one: the local E5 model needs the `query:` prefix, which is why the indexer asks for it) and the nearest entities above a similarity threshold, at most three, are added to the entities its words matched. The walk then proceeds as before: bounded depth, node limit and fan-out, reviewed relations only. A near miss costs one extra neighbourhood in the context given to the model; it can not create a citation, because every citation is validated against the transcript segments independently.

**Failure never costs an answer.** Embedding entities is best effort: a model problem is logged by type and the analysis completes; the entities stay unindexed and the next analysis of the owner, a sync import, or index maintenance (finding `entity_vectors_missing`, once per owner per run) catches up. A question that can not be embedded simply has no semantic seeds.

**Exact search, no approximate index.** Within one owner the vectors are scanned exactly (workspace and owner lead the key). That is milliseconds for tens of thousands of entities per owner and avoids an index that needs a fixed vector size. If an owner ever has millions, an HNSW index per model is the upgrade.

**The threshold belongs to the model.** Cosine scores are not comparable between models: the local E5 model puts every pair between about 0.68 and 0.86, an OpenAI embedding model spreads from near 0 to near 1. `python -m nodo_api.admin calibrate-entities` embeds 8 labels and 28 questions (24 about the labels in English, Italian, Polish and Danish, 4 about nothing) and prints how often the nearest entity is the right one, the score distributions and a suggested threshold; `NODO_ENTITY_MIN_SIMILARITY` overrides the default. Measured on the local model (24 questions): the right entity is the nearest 22 times (92%); the right entity scores 0.72 to 0.86 (median 0.81); the best score of a question about nothing is at most 0.78. The local default is therefore 0.78. The Azure default (0.35) is an expectation for `text-embedding-3` and is the first thing to calibrate after connecting.

## Alternatives considered
- **Translate the question into the meeting's language first.** One more model call per question and a language per meeting to track. Vectors give the same result without either.
- **Add the entities to the search index.** Search documents are transcript segments with the access filter inside the query; a second kind of document would need its own filter and erasure path. The graph already has both.
- **Let the language model pick the entities.** Spends tokens on every question to do what a vector lookup does in a millisecond, and its choices would need validating against the graph anyway.
- **A higher threshold, no semantic seeds for doubtful matches.** Loses the cross-language case, which is the point. With a limit of three seeds the cost of a miss is small.

## Consequences
- 22 of 24 on a hand-written set is encouraging and not a guarantee: the two misses are short labels against long questions in Italian. The golden set (eval/) should gain graph-seed questions per language before the threshold is fixed for production.
- Embedding entity labels adds one batched embedding call per analysis (a handful of labels). The capacity model's embedding tokens grow by well under 1%.
- Entities created before this change have no vectors until the next analysis or the weekly index check; the check reports them and `reindex` fixes them.
