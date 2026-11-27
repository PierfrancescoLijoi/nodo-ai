-- Collections (FR-4, FR-8): owner-made, nested folders of sources. Structure, never inferred.
-- Deleting a collection deletes its subtree and memberships, never a source. Nesting follows the
-- parent-link rule of ADR-030 (no cycle, at most six links), checked by the store when it writes.
-- `normalized` is the case, accent and spacing insensitive name: siblings may not share it.

CREATE TABLE collections (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    id           TEXT NOT NULL,
    parent_id    TEXT,
    name         TEXT NOT NULL,
    normalized   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, parent_id) REFERENCES collections (workspace_id, id)
        ON DELETE CASCADE
);
CREATE UNIQUE INDEX collections_sibling_name_idx
    ON collections (workspace_id, owner_id, COALESCE(parent_id, ''), normalized);
CREATE INDEX collections_parent_idx ON collections (workspace_id, parent_id);

CREATE TABLE source_collections (
    workspace_id  TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (workspace_id, collection_id, source_id),
    FOREIGN KEY (workspace_id, collection_id) REFERENCES collections (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX source_collections_source_idx ON source_collections (workspace_id, source_id);

-- Backstop for every tombstone path (documents, and meetings through the 0009 triggers): a
-- deleted source is never left listed in a collection.
CREATE TRIGGER source_collections_forget AFTER UPDATE OF tombstoned_at ON sources
WHEN NEW.tombstoned_at IS NOT NULL
BEGIN
    DELETE FROM source_collections WHERE workspace_id = NEW.workspace_id AND source_id = NEW.id;
END;
