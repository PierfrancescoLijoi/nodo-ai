-- What has already been copied to which cloud deployment, so a sync only sends what changed (ADR-015).
CREATE TABLE sync_state (
    workspace_id TEXT NOT NULL,
    meeting_id   TEXT NOT NULL,
    cloud_url    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, meeting_id, cloud_url)
);
