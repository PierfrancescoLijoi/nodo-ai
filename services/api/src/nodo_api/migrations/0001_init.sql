-- NODO AI source of truth. Every table carries workspace_id and is protected by row level security
-- keyed on the transaction setting app.workspace_id (ARCHITECTURE.md section 5.1). Search and graph
-- rows are projections that can be rebuilt from these tables (ADR-007).

CREATE TABLE meetings (
    workspace_id   text        NOT NULL,
    id             text        NOT NULL,
    owner_id       text        NOT NULL,
    state          text        NOT NULL
        CHECK (state IN ('uploading', 'transcribing', 'extracting', 'review', 'ready', 'failed')),
    project        text,
    created_at     timestamptz NOT NULL,
    tombstoned_at  timestamptz,
    PRIMARY KEY (workspace_id, id)
);

-- A user declaration for one meeting. It documents what the user stated; it is not participant
-- consent and creates no legal basis (ADR-005).
CREATE TABLE recording_attestations (
    workspace_id        text        NOT NULL,
    meeting_id          text        NOT NULL,
    user_id             text        NOT NULL,
    notice_version      text        NOT NULL,
    locale              text        NOT NULL CHECK (locale IN ('en', 'pl', 'it', 'da')),
    retention_policy_id text        NOT NULL,
    acknowledged_at     timestamptz NOT NULL,
    audit_event_id      text        NOT NULL,
    signature           text        NOT NULL,
    PRIMARY KEY (workspace_id, meeting_id),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE idempotency_keys (
    workspace_id text NOT NULL,
    user_id      text NOT NULL,
    key          text NOT NULL,
    meeting_id   text NOT NULL,
    request_hash text NOT NULL,
    PRIMARY KEY (workspace_id, user_id, key)
);

CREATE TABLE transcript_segments (
    workspace_id text             NOT NULL,
    meeting_id   text             NOT NULL,
    version      integer          NOT NULL CHECK (version > 0),
    id           text             NOT NULL,
    speaker_id   text             NOT NULL,
    start_ms     integer          NOT NULL CHECK (start_ms >= 0),
    end_ms       integer          NOT NULL CHECK (end_ms >= start_ms),
    text         text             NOT NULL,
    confidence   double precision NOT NULL,
    locale       text             NOT NULL,
    project      text,
    PRIMARY KEY (workspace_id, meeting_id, version, id),
    FOREIGN KEY (workspace_id, meeting_id) REFERENCES meetings (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE graph_nodes (
    workspace_id text NOT NULL,
    id           text NOT NULL,
    label        text NOT NULL,
    meeting_id   text NOT NULL,
    segment_id   text NOT NULL,
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE audit_events (
    workspace_id text        NOT NULL,
    id           text        NOT NULL,
    event_type   text        NOT NULL,
    metadata     jsonb       NOT NULL,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

-- Transactional outbox: a job exists once per (workspace, key, type) and is published to Service Bus
-- afterwards, so a crash between the two never loses or duplicates the work.
CREATE TABLE jobs (
    workspace_id text        NOT NULL,
    job_key      text        NOT NULL,
    job_type     text        NOT NULL,
    id           text        NOT NULL,
    payload      jsonb       NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    PRIMARY KEY (workspace_id, job_key, job_type)
);

CREATE INDEX meetings_owner_idx ON meetings (workspace_id, owner_id) WHERE tombstoned_at IS NULL;
CREATE INDEX segments_meeting_idx ON transcript_segments (workspace_id, meeting_id, version);
CREATE INDEX graph_nodes_label_idx ON graph_nodes (workspace_id, lower(label));
CREATE INDEX jobs_unpublished_idx ON jobs (workspace_id, created_at) WHERE published_at IS NULL;

ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE recording_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

ALTER TABLE meetings FORCE ROW LEVEL SECURITY;
ALTER TABLE recording_attestations FORCE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE transcript_segments FORCE ROW LEVEL SECURITY;
ALTER TABLE graph_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON meetings
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON recording_attestations
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON idempotency_keys
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON transcript_segments
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON graph_nodes
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON audit_events
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON jobs
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
