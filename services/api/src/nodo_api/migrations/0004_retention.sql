-- Audio retention (ADR-019). The person chooses 7, 30 or 90 days for the audio of each meeting; a daily
-- job erases the audio when the time is up and records that it did. The job runs as its own identity
-- that may look across workspaces for exactly this purpose and for nothing else:
--   * it can read meetings and their attestation (ids, dates, retention policy), never transcripts,
--     graph or search data;
--   * it can set audio_erased_at on a meeting, never anything else;
--   * everything else it does (the audit event) goes through the normal per-workspace path.

ALTER TABLE meetings ADD COLUMN audio_erased_at timestamptz;

CREATE INDEX meetings_audio_due_idx ON meetings (created_at)
    WHERE audio_erased_at IS NULL AND tombstoned_at IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nodo_retention') THEN
        CREATE ROLE nodo_retention NOLOGIN;
    END IF;
END
$$;

GRANT SELECT ON meetings, recording_attestations TO nodo_retention;
GRANT UPDATE (audio_erased_at) ON meetings TO nodo_retention;

CREATE POLICY retention_scan ON meetings FOR SELECT TO nodo_retention USING (true);
CREATE POLICY retention_scan ON recording_attestations FOR SELECT TO nodo_retention USING (true);
CREATE POLICY retention_mark ON meetings FOR UPDATE TO nodo_retention
    USING (audio_erased_at IS NULL) WITH CHECK (audio_erased_at IS NOT NULL);
