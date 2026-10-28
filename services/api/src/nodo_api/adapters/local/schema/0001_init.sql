-- Local database. Same tables and meaning as the PostgreSQL schema (migrations 0001 to 0003), written
-- for SQLite: timestamps are ISO 8601 UTC text (they sort correctly as text), ids are text, JSON is
-- text. There is no row level security: the file belongs to one person on one machine, and every
-- query still filters by (workspace_id, owner_id) so behaviour matches the cloud backend.

CREATE TABLE meetings (
    workspace_id  TEXT NOT NULL,
    id            TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    state         TEXT NOT NULL
        CHECK (state IN ('uploading', 'transcribing', 'extracting', 'review', 'ready', 'failed')),
    project       TEXT,
    created_at    TEXT NOT NULL,
    tombstoned_at TEXT,
    title         TEXT,
    summary       TEXT,
    duration_ms   INTEGER,
    speaker_names TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (workspace_id, id)
);
CREATE INDEX meetings_list_idx ON meetings (workspace_id, owner_id, created_at DESC, id DESC)
    WHERE tombstoned_at IS NULL;

CREATE TABLE recording_attestations (
    workspace_id        TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    notice_version      TEXT NOT NULL,
    locale              TEXT NOT NULL CHECK (locale IN ('en', 'pl', 'it', 'da')),
    retention_policy_id TEXT NOT NULL,
    acknowledged_at     TEXT NOT NULL,
    audit_event_id      TEXT NOT NULL,
    signature           TEXT NOT NULL,
    PRIMARY KEY (workspace_id, meeting_id),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE idempotency_keys (
    workspace_id TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    key          TEXT NOT NULL,
    meeting_id   TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id, key)
);

CREATE TABLE transcript_segments (
    workspace_id TEXT    NOT NULL,
    meeting_id   TEXT    NOT NULL,
    version      INTEGER NOT NULL CHECK (version > 0),
    id           TEXT    NOT NULL,
    speaker_id   TEXT    NOT NULL,
    start_ms     INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms       INTEGER NOT NULL CHECK (end_ms >= start_ms),
    text         TEXT    NOT NULL,
    confidence   REAL    NOT NULL,
    locale       TEXT    NOT NULL,
    project      TEXT,
    PRIMARY KEY (workspace_id, meeting_id, version, id),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE audit_events (
    workspace_id TEXT NOT NULL,
    id           TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    metadata     TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id)
);

-- The queue is this table. A worker thread claims a row, runs it, and marks it done or failed.
CREATE TABLE jobs (
    workspace_id  TEXT NOT NULL,
    job_key       TEXT NOT NULL,
    job_type      TEXT NOT NULL,
    id            TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    run_after     TEXT NOT NULL,
    claimed_at    TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    done_at       TEXT,
    failed_reason TEXT,
    PRIMARY KEY (workspace_id, job_key, job_type)
);
CREATE INDEX jobs_ready_idx ON jobs (run_after) WHERE done_at IS NULL AND failed_reason IS NULL;

-- Upload links: a one-time token lets the client PUT the recording straight into the engine.
CREATE TABLE upload_sessions (
    token        TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    meeting_id   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT
);

-- Knowledge graph (ADR-012), same model as the PostgreSQL tables.
CREATE TABLE entities (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    id           TEXT NOT NULL,
    type         TEXT NOT NULL
        CHECK (type IN ('person', 'project', 'system', 'topic', 'decision', 'action', 'meeting')),
    label        TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 240),
    normalized   TEXT NOT NULL,
    status       TEXT,
    effective_at TEXT,
    due_at       TEXT,
    ref          TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, id)
) WITHOUT ROWID;
CREATE INDEX entities_recent_idx ON entities (workspace_id, owner_id, updated_at DESC)
    WHERE type <> 'meeting';

CREATE TABLE relations (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    id           TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    kind         TEXT NOT NULL
        CHECK (kind IN ('MENTIONED_IN', 'ABOUT', 'DECIDED_IN', 'PROPOSED_BY', 'ASSIGNED_TO',
                        'DEPENDS_ON', 'SUPERSEDES', 'CONTRADICTS')),
    status       TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    confidence   REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    valid_from   TEXT,
    valid_to     TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, id),
    CHECK (source_id <> target_id)
) WITHOUT ROWID;
CREATE INDEX relations_out_idx ON relations (workspace_id, owner_id, source_id, confidence DESC, id)
    WHERE status <> 'rejected';
CREATE INDEX relations_in_idx ON relations (workspace_id, owner_id, target_id, confidence DESC, id)
    WHERE status <> 'rejected';
CREATE INDEX relations_changes_idx ON relations (workspace_id, owner_id, created_at DESC)
    WHERE kind = 'SUPERSEDES' AND status <> 'rejected';

CREATE TABLE relation_evidence (
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    relation_id  TEXT NOT NULL,
    meeting_id   TEXT NOT NULL,
    segment_id   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, relation_id, meeting_id, segment_id),
    FOREIGN KEY (workspace_id, owner_id, relation_id)
        REFERENCES relations (workspace_id, owner_id, id) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX evidence_meeting_idx ON relation_evidence (workspace_id, meeting_id);

-- Search: this table carries the text and the embedding (vector ranking); the FTS5 index over the
-- text gives BM25 ranking. Both are projections of the transcript and can be rebuilt (ADR-007).
CREATE TABLE search_segments (
    rowid        INTEGER PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    meeting_id   TEXT NOT NULL,
    segment_id   TEXT NOT NULL,
    speaker_id   TEXT NOT NULL,
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    version      INTEGER NOT NULL,
    confidence   REAL NOT NULL,
    project      TEXT,
    locale       TEXT NOT NULL,
    text         TEXT NOT NULL,
    vector       BLOB NOT NULL,
    UNIQUE (workspace_id, meeting_id, segment_id)
);
CREATE INDEX search_owner_idx ON search_segments (workspace_id, owner_id);
CREATE VIRTUAL TABLE search_fts USING fts5(
    text, content='search_segments', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
