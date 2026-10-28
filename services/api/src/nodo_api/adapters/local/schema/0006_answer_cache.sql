-- Answers already given, found again by the meaning of the question (ADR-026); same tables as the
-- cloud schema. Vectors are float32 bytes; the nearest is found by an exact product over the owner's
-- (at most a few hundred) entries.
CREATE TABLE answer_cache_epoch (
    workspace_id TEXT    NOT NULL,
    owner_id     TEXT    NOT NULL,
    epoch        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (workspace_id, owner_id)
) WITHOUT ROWID;

CREATE TABLE answer_cache (
    workspace_id     TEXT    NOT NULL,
    owner_id         TEXT    NOT NULL,
    version          TEXT    NOT NULL,
    locale           TEXT    NOT NULL,
    filters          TEXT    NOT NULL,
    question_key     TEXT    NOT NULL,
    epoch            INTEGER NOT NULL,
    dims             INTEGER NOT NULL,
    vector           BLOB    NOT NULL,
    answer           TEXT    NOT NULL,
    answerability    TEXT    NOT NULL,
    citations        TEXT    NOT NULL,
    related_entities TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, version, locale, filters, question_key)
);
CREATE INDEX answer_cache_recent ON answer_cache (workspace_id, owner_id, created_at DESC);
