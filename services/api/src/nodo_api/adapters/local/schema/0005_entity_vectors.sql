-- Finding entities by meaning (ADR-024), same table as the cloud schema. Vectors are float32 bytes;
-- the nearest are found by an exact product over the owner's vectors.
CREATE TABLE entity_vectors (
    workspace_id TEXT    NOT NULL,
    owner_id     TEXT    NOT NULL,
    entity_id    TEXT    NOT NULL,
    dims         INTEGER NOT NULL,
    vector       BLOB    NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, entity_id),
    FOREIGN KEY (workspace_id, owner_id, entity_id)
        REFERENCES entities (workspace_id, owner_id, id) ON DELETE CASCADE
) WITHOUT ROWID;
