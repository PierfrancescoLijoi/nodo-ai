-- Sources, sections and fragments (ADR-029). A source is one thing put into the knowledge base; a
-- meeting is one kind of source and keeps its id. The meetings table stays the meeting detail table:
-- triggers below copy the shared columns into sources, so no Python meeting code changes and every
-- write path (add, save, a future import) is covered. Fragments of a meeting stay in
-- transcript_segments; this table holds the fragments of the other kinds.
-- Timestamps are ISO 8601 UTC text, as in the rest of the local database.

CREATE TABLE sources (
    workspace_id  TEXT    NOT NULL,
    owner_id      TEXT    NOT NULL,
    id            TEXT    NOT NULL,
    kind          TEXT    NOT NULL
        CHECK (kind IN ('meeting', 'document', 'deck', 'web', 'note', 'audio')),
    title         TEXT,
    state         TEXT    NOT NULL,
    origin        TEXT,
    language      TEXT,
    content_hash  TEXT,
    version       INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    TEXT    NOT NULL,
    tombstoned_at TEXT,
    PRIMARY KEY (workspace_id, id)
);
CREATE INDEX sources_list_idx ON sources (workspace_id, owner_id, created_at DESC, id DESC)
    WHERE tombstoned_at IS NULL;

CREATE TABLE sections (
    workspace_id TEXT    NOT NULL,
    source_id    TEXT    NOT NULL,
    id           TEXT    NOT NULL,
    parent_id    TEXT,
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    level        INTEGER NOT NULL CHECK (level >= 1),
    title        TEXT    NOT NULL,
    PRIMARY KEY (workspace_id, source_id, id),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE fragments (
    workspace_id TEXT    NOT NULL,
    source_id    TEXT    NOT NULL,
    version      INTEGER NOT NULL CHECK (version >= 1),
    id           TEXT    NOT NULL,
    section_id   TEXT,
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    text         TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 20000),
    locator      TEXT    NOT NULL CHECK (json_valid(locator)),
    speaker_id   TEXT,
    confidence   REAL,
    locale       TEXT,
    PRIMARY KEY (workspace_id, source_id, version, id),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);

-- Meetings that exist already.
INSERT INTO sources (workspace_id, owner_id, id, kind, title, state, created_at, tombstoned_at)
SELECT workspace_id, owner_id, id, 'meeting', title, state, created_at, tombstoned_at
FROM meetings;

-- A new meeting becomes a source with the same id. The upsert keeps INSERT OR REPLACE on meetings
-- from failing on the source that is already there (it never touches a source of another kind).
CREATE TRIGGER meetings_to_sources_insert AFTER INSERT ON meetings
BEGIN
    INSERT INTO sources (workspace_id, owner_id, id, kind, title, state, created_at, tombstoned_at)
    VALUES (NEW.workspace_id, NEW.owner_id, NEW.id, 'meeting', NEW.title, NEW.state, NEW.created_at,
            NEW.tombstoned_at)
    ON CONFLICT (workspace_id, id) DO UPDATE SET
        owner_id = excluded.owner_id, title = excluded.title, state = excluded.state,
        tombstoned_at = excluded.tombstoned_at
    WHERE sources.kind = 'meeting';
END;

-- Only title, state and tombstone move across; project and the other meeting columns stay in meetings.
CREATE TRIGGER meetings_to_sources_update AFTER UPDATE OF title, state, tombstoned_at ON meetings
BEGIN
    UPDATE sources SET title = NEW.title, state = NEW.state, tombstoned_at = NEW.tombstoned_at
    WHERE workspace_id = NEW.workspace_id AND id = NEW.id AND kind = 'meeting';
END;

-- Deleting the source also deletes its sections and fragments (ON DELETE CASCADE).
CREATE TRIGGER meetings_to_sources_delete AFTER DELETE ON meetings
BEGIN
    DELETE FROM sources WHERE workspace_id = OLD.workspace_id AND id = OLD.id AND kind = 'meeting';
END;
