-- Data exports (ADR-019): a person can ask for everything NODO holds about them (GDPR articles 15 and
-- 20). The export is built by a job and kept for a day.

CREATE TABLE exports (
    workspace_id text        NOT NULL,
    id           text        NOT NULL,
    owner_id     text        NOT NULL,
    meeting_id   text,
    state        text        NOT NULL CHECK (state IN ('queued', 'running', 'ready', 'failed')),
    blob_name    text,
    size_bytes   integer,
    created_at   timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id)
);

CREATE INDEX exports_owner_idx ON exports (workspace_id, owner_id, created_at DESC);

ALTER TABLE exports ENABLE ROW LEVEL SECURITY;
ALTER TABLE exports FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON exports
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
