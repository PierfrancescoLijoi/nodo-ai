-- Index maintenance (ADR-021). A job compares the search index and the graph with the store, meeting
-- by meeting, and repairs the difference. Listing the meetings has to look across workspaces; that
-- is the one thing this role may do outside a workspace, and only with the columns it needs:
--   * it lists meetings (ids, owner, state, deleted or not), never titles, summaries or transcripts;
--   * everything else it reads or writes (transcript segments, graph evidence, the job outbox, the
--     audit trail) goes through the normal per-workspace path, so row level security still binds it
--     to one workspace at a time;
--   * it can remove graph rows and search documents of deleted meetings, and ask for a re-analysis
--     through the outbox; it can not change a meeting, a transcript or an attestation.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nodo_maintenance') THEN
        CREATE ROLE nodo_maintenance NOLOGIN;
    END IF;
END
$$;

GRANT SELECT (workspace_id, id, owner_id, state, tombstoned_at) ON meetings TO nodo_maintenance;
CREATE POLICY maintenance_scan ON meetings FOR SELECT TO nodo_maintenance USING (true);

GRANT SELECT ON transcript_segments TO nodo_maintenance;
GRANT SELECT, UPDATE, DELETE ON entities, relations TO nodo_maintenance;
GRANT SELECT, DELETE ON relation_evidence TO nodo_maintenance;
GRANT SELECT, INSERT, UPDATE ON jobs TO nodo_maintenance;
GRANT INSERT ON audit_events TO nodo_maintenance;
