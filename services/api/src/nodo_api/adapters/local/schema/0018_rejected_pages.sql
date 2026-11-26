-- nodo:foreign-keys-off
-- SQLite cannot alter a CHECK constraint in place. Rebuild pages while retaining its children.
CREATE TABLE pages_new (
    workspace_id      TEXT NOT NULL,
    id                TEXT NOT NULL,
    owner_id          TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('concept', 'source_summary', 'saved_answer')),
    subject_id        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'stale')),
    current_revision  INTEGER,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id),
    CHECK ((status IN ('proposed', 'rejected') AND current_revision IS NULL)
        OR (status IN ('approved', 'stale') AND current_revision >= 1))
);
INSERT INTO pages_new SELECT * FROM pages;
DROP TABLE pages;
ALTER TABLE pages_new RENAME TO pages;
CREATE INDEX pages_owner_created ON pages (workspace_id, owner_id, created_at DESC, id);
