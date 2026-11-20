-- Search over the fragments of documents and other non-meeting sources (Task 8). Same shape as the
-- meeting index (search_segments, search_fts): this table carries the text, the metadata a hit
-- needs and the embedding; the FTS5 index over the text gives BM25 ranking. Both are projections of
-- the fragments table and can be rebuilt. Meeting search is not touched.
--
-- AUTOINCREMENT: a rowid is never reused, so (count, max rowid) of an owner's rows changes whenever
-- the set of rows changes. The vector cache relies on it to notice deletes made by the triggers.
-- Timestamps and JSON are text, as in the rest of the local database.

CREATE TABLE search_fragments (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT    NOT NULL,
    owner_id     TEXT    NOT NULL,
    source_id    TEXT    NOT NULL,
    fragment_id  TEXT    NOT NULL,
    version      INTEGER NOT NULL CHECK (version >= 1),
    section_id   TEXT,
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    locator      TEXT    NOT NULL CHECK (json_valid(locator)),
    speaker_id   TEXT,
    confidence   REAL,
    locale       TEXT,
    text         TEXT    NOT NULL,
    -- 1 when the text is over the embedding ceiling (512 tokens): the text index holds all of it,
    -- the vector stands for the first 512 tokens.
    truncated    INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    vector       BLOB    NOT NULL,
    UNIQUE (workspace_id, source_id, fragment_id)
);
CREATE INDEX search_fragments_owner_idx ON search_fragments (workspace_id, owner_id);
CREATE INDEX search_fragments_source_idx ON search_fragments (workspace_id, source_id);

-- The FTS index follows the table through triggers, so every way a row can go (the index code, the
-- triggers below, a cascade) leaves no dangling entry. Rows are only inserted and deleted.
CREATE VIRTUAL TABLE fragment_fts USING fts5(
    text, content='search_fragments', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER search_fragments_fts_insert AFTER INSERT ON search_fragments
BEGIN
    INSERT INTO fragment_fts (rowid, text) VALUES (NEW.rowid, NEW.text);
END;
CREATE TRIGGER search_fragments_fts_delete AFTER DELETE ON search_fragments
BEGIN
    INSERT INTO fragment_fts (fragment_fts, rowid, text) VALUES ('delete', OLD.rowid, OLD.text);
END;

-- Backstops: the import and delete services erase the index explicitly, and these make sure that a
-- deleted source, a tombstoned source or an erased fragment can not stay searchable even if a code
-- path forgets to. A row is dropped with the fragment row it mirrors (same source, id and version).
CREATE TRIGGER fragments_drop_index_rows AFTER DELETE ON fragments
BEGIN
    DELETE FROM search_fragments
    WHERE workspace_id = OLD.workspace_id AND source_id = OLD.source_id
      AND fragment_id = OLD.id AND version = OLD.version;
END;
CREATE TRIGGER sources_tombstone_drop_index_rows AFTER UPDATE OF tombstoned_at ON sources
WHEN NEW.tombstoned_at IS NOT NULL
BEGIN
    DELETE FROM search_fragments WHERE workspace_id = NEW.workspace_id AND source_id = NEW.id;
END;
CREATE TRIGGER sources_delete_drop_index_rows AFTER DELETE ON sources
BEGIN
    DELETE FROM search_fragments WHERE workspace_id = OLD.workspace_id AND source_id = OLD.id;
END;
