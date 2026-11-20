-- Imports of documents and other files into the knowledge base (ADR-029, ADR-031).
-- import_jobs: the progress a person sees and the worker updates, one row per source. It follows its
--   source: deleting the source row deletes the job.
-- source_versions: what each version of a source was made from. original_name is local-only and no
--   API returns it. The row is written by the import, so a version with no fragments (a scan with no
--   text layer) can still be recorded.
-- The unique index makes the same file, imported twice by one owner, one live source: a second
--   insert fails and the service answers "unchanged". Meetings are outside the rule, and so is a
--   source with no hash yet or one that was deleted.
-- Timestamps are ISO 8601 UTC text, as in the rest of the local database.

CREATE TABLE import_jobs (
    workspace_id     TEXT    NOT NULL,
    owner_id         TEXT    NOT NULL,
    source_id        TEXT    NOT NULL,
    state            TEXT    NOT NULL
        CHECK (state IN ('queued', 'parsing', 'indexing', 'extracting', 'ready', 'failed',
                         'cancelled', 'awaiting_confirmation')),
    step             TEXT,
    done             INTEGER NOT NULL DEFAULT 0 CHECK (done >= 0),
    total            INTEGER NOT NULL DEFAULT 0 CHECK (total >= 0),
    attempt          INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    error_code       TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    PRIMARY KEY (workspace_id, source_id),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE source_versions (
    workspace_id       TEXT    NOT NULL,
    source_id          TEXT    NOT NULL,
    version            INTEGER NOT NULL CHECK (version >= 1),
    content_hash       TEXT    NOT NULL,
    parser             TEXT    NOT NULL,
    extraction_version TEXT,
    original_name      TEXT,
    size_bytes         INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at         TEXT    NOT NULL,
    PRIMARY KEY (workspace_id, source_id, version),
    FOREIGN KEY (workspace_id, source_id) REFERENCES sources (workspace_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX sources_live_hash_idx ON sources (workspace_id, owner_id, content_hash)
    WHERE tombstoned_at IS NULL AND content_hash IS NOT NULL AND kind != 'meeting';
