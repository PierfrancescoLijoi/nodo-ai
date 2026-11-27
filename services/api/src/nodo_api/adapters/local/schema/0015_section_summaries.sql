-- Derived summaries are version-pinned. Source deletion cascades them; a new source version
-- cannot accidentally reuse prose generated for old content.
CREATE TABLE section_summaries (
    workspace_id   TEXT    NOT NULL,
    source_id      TEXT    NOT NULL,
    section_id     TEXT    NOT NULL,
    source_version INTEGER NOT NULL CHECK (source_version >= 1),
    model_version  TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL,
    text           TEXT    NOT NULL CHECK (length(text) > 0),
    token_count    INTEGER NOT NULL CHECK (token_count >= 1),
    PRIMARY KEY (workspace_id, source_id, section_id, source_version),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);
