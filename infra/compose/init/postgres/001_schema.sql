-- Local POC schema. workspace_id is the partition key of every table (ARCHITECTURE.md section 5.1).
-- Row level security is FORCED so that even the table owner is filtered; the API must connect with
-- a non-superuser role and run `SET LOCAL app.workspace_id = '<uuid>'` inside each transaction.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE meetings (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    title text NOT NULL,
    spoken_locales text[] NOT NULL DEFAULT ARRAY['en'],
    state text NOT NULL DEFAULT 'uploading'
        CHECK (state IN ('uploading', 'transcribing', 'extracting', 'review', 'ready', 'failed')),
    started_at timestamptz NOT NULL,
    retention_until timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE transcript_segments (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    speaker_label text NOT NULL,
    started_at_ms integer NOT NULL CHECK (started_at_ms >= 0),
    ended_at_ms integer NOT NULL CHECK (ended_at_ms >= started_at_ms),
    content text NOT NULL,
    content_locale text NOT NULL DEFAULT 'en',
    tombstoned_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id, version),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

-- A user declaration for one meeting. It documents what the user stated; it is not participant
-- consent and does not create a legal basis (ADR-005).
CREATE TABLE recording_attestations (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    user_id uuid NOT NULL,
    notice_version text NOT NULL,
    locale text NOT NULL CHECK (locale IN ('en', 'pl', 'it', 'da')),
    retention_policy_id text NOT NULL,
    acknowledged_at timestamptz NOT NULL,
    audit_event_id uuid NOT NULL,
    signature text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE outbox (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, idempotency_key)
);

CREATE INDEX meetings_workspace_started_idx
    ON meetings (workspace_id, started_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX transcript_segments_workspace_meeting_idx
    ON transcript_segments (workspace_id, meeting_id, started_at_ms);
CREATE INDEX transcript_segments_workspace_live_idx
    ON transcript_segments (workspace_id, meeting_id)
    WHERE tombstoned_at IS NULL;
CREATE INDEX recording_attestations_workspace_meeting_idx
    ON recording_attestations (workspace_id, meeting_id);
CREATE INDEX outbox_workspace_pending_idx
    ON outbox (workspace_id, occurred_at)
    WHERE published_at IS NULL;

ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE recording_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE meetings FORCE ROW LEVEL SECURITY;
ALTER TABLE transcript_segments FORCE ROW LEVEL SECURITY;
ALTER TABLE recording_attestations FORCE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY meetings_workspace_policy ON meetings
    USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
CREATE POLICY transcript_segments_workspace_policy ON transcript_segments
    USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
CREATE POLICY recording_attestations_workspace_policy ON recording_attestations
    USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
CREATE POLICY outbox_workspace_policy ON outbox
    USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
