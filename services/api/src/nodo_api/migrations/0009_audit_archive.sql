-- Audit archive (ADR-022). An hourly job copies audit events to storage with a time-based retention
-- policy. It reads events across workspaces (identifiers and event types, never content) and keeps
-- one row of its own: where the last batch ended and the hash of the file that ended there.
--
-- The application roles must not touch that row: moving the cursor forward would hide events from
-- the archive. grant_roles revokes it from them; only nodo_audit may read and update it.

CREATE INDEX audit_events_time_idx ON audit_events (occurred_at, workspace_id, id);

CREATE TABLE audit_archive_state (
    id          smallint    PRIMARY KEY CHECK (id = 1),
    occurred_at timestamptz,
    workspace_id text,
    event_id    text,
    last_sha256 text        NOT NULL DEFAULT '',
    archived    bigint      NOT NULL DEFAULT 0
);
INSERT INTO audit_archive_state (id) VALUES (1);
REVOKE ALL ON audit_archive_state FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nodo_audit') THEN
        CREATE ROLE nodo_audit NOLOGIN;
    END IF;
END
$$;

GRANT SELECT ON audit_events TO nodo_audit;
CREATE POLICY audit_scan ON audit_events FOR SELECT TO nodo_audit USING (true);
GRANT SELECT, UPDATE ON audit_archive_state TO nodo_audit;
