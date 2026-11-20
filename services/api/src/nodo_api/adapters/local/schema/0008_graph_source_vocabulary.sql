-- nodo:foreign-keys-off
-- Graph vocabulary for sources (ADR-029) and the concept hierarchy (ADR-030): entities may be of
-- type 'source' and relations of kind 'CHILD_OF'. SQLite can not alter a CHECK constraint, so both
-- tables are rebuilt (https://www.sqlite.org/lang_altertable.html, "other kinds of table schema
-- changes"): create the new table, copy, drop the old one, rename the new one, recreate indexes.
-- The runner turns foreign keys off around this script (the header above) so dropping `relations`
-- and `entities` does not cascade into relation_evidence and entity_vectors, and runs
-- PRAGMA foreign_key_check before it commits. The child tables keep their text
-- `REFERENCES relations` / `REFERENCES entities`, which names the rebuilt tables after the rename.
-- Ids and every column value are copied unchanged. No view or trigger names these tables.

CREATE TABLE entities_new (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    id           TEXT NOT NULL,
    type         TEXT NOT NULL
        CHECK (type IN ('person', 'project', 'system', 'topic', 'decision', 'action', 'meeting',
                        'source')),
    label        TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 240),
    normalized   TEXT NOT NULL,
    status       TEXT,
    effective_at TEXT,
    due_at       TEXT,
    ref          TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, id)
) WITHOUT ROWID;
INSERT INTO entities_new (workspace_id, owner_id, id, type, label, normalized, status,
                          effective_at, due_at, ref, updated_at)
    SELECT workspace_id, owner_id, id, type, label, normalized, status,
           effective_at, due_at, ref, updated_at
    FROM entities;
DROP TABLE entities;
ALTER TABLE entities_new RENAME TO entities;
CREATE INDEX entities_recent_idx ON entities (workspace_id, owner_id, updated_at DESC)
    WHERE type NOT IN ('meeting', 'source');

CREATE TABLE relations_new (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    id           TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    kind         TEXT NOT NULL
        CHECK (kind IN ('MENTIONED_IN', 'ABOUT', 'DECIDED_IN', 'PROPOSED_BY', 'ASSIGNED_TO',
                        'DEPENDS_ON', 'SUPERSEDES', 'CONTRADICTS', 'CHILD_OF')),
    status       TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    confidence   REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    valid_from   TEXT,
    valid_to     TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, id),
    CHECK (source_id <> target_id)
) WITHOUT ROWID;
INSERT INTO relations_new (workspace_id, owner_id, id, source_id, target_id, kind, status,
                           confidence, valid_from, valid_to, created_at)
    SELECT workspace_id, owner_id, id, source_id, target_id, kind, status,
           confidence, valid_from, valid_to, created_at
    FROM relations;
DROP TABLE relations;
ALTER TABLE relations_new RENAME TO relations;
CREATE INDEX relations_out_idx ON relations (workspace_id, owner_id, source_id, confidence DESC, id)
    WHERE status <> 'rejected';
CREATE INDEX relations_in_idx ON relations (workspace_id, owner_id, target_id, confidence DESC, id)
    WHERE status <> 'rejected';
CREATE INDEX relations_changes_idx ON relations (workspace_id, owner_id, created_at DESC)
    WHERE kind = 'SUPERSEDES' AND status <> 'rejected';
