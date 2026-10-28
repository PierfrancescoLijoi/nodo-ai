-- Answers already given, found again by the meaning of the question (ADR-026).
--
-- One entry per question asked, scoped to a workspace AND an owner: a person's entries serve that
-- person's questions only. An entry is usable only while the owner's epoch (the second table) is the
-- one it was written under; every change to the owner's meetings, transcripts or graph raises the
-- epoch and deletes the entries, and a write computed under an older epoch is refused, so a question
-- that was being answered while a meeting was deleted can not store what it found.
--
-- The entry holds the answer and the quotes it cites, which are transcript text: deleting a meeting
-- deletes the owner's entries in the same operation, and an entry older than the configured lifetime
-- is removed by the next write. The vector has no fixed size on purpose (the embedding model can
-- change; an entry of another size is never compared and ages out), the same choice as entity_vectors.

CREATE TABLE answer_cache_epoch (
    workspace_id text   NOT NULL,
    owner_id     text   NOT NULL,
    epoch        bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (workspace_id, owner_id)
);

CREATE TABLE answer_cache (
    workspace_id     text        NOT NULL,
    owner_id         text        NOT NULL,
    version          text        NOT NULL,
    locale           text        NOT NULL,
    filters          text        NOT NULL,
    question_key     text        NOT NULL,
    epoch            bigint      NOT NULL,
    dims             integer     NOT NULL,
    vector           vector      NOT NULL,
    answer           text        NOT NULL,
    answerability    text        NOT NULL,
    citations        jsonb       NOT NULL,
    related_entities jsonb       NOT NULL,
    created_at       timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, version, locale, filters, question_key)
);
CREATE INDEX answer_cache_recent ON answer_cache (workspace_id, owner_id, created_at DESC);

ALTER TABLE answer_cache_epoch ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_cache_epoch FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON answer_cache_epoch
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));

ALTER TABLE answer_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_cache FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON answer_cache
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));

-- Index maintenance repairs what search finds, so it must be able to say that the owner's cached
-- answers are out of date: raise the epoch and drop the entries. Nothing else.
GRANT SELECT, INSERT, UPDATE ON answer_cache_epoch TO nodo_maintenance;
-- A DELETE ... WHERE needs to read the columns it filters on, and only those.
GRANT SELECT (workspace_id, owner_id) ON answer_cache TO nodo_maintenance;
GRANT DELETE ON answer_cache TO nodo_maintenance;
