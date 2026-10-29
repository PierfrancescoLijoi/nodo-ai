-- Data exports (ADR-019), same table as the cloud schema.
CREATE TABLE exports (
    workspace_id TEXT NOT NULL,
    id           TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    meeting_id   TEXT,
    state        TEXT NOT NULL CHECK (state IN ('queued', 'running', 'ready', 'failed')),
    blob_name    TEXT,
    size_bytes   INTEGER,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id)
);
CREATE INDEX exports_owner_idx ON exports (workspace_id, owner_id, created_at DESC);
