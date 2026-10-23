-- Speech to text in stages (docs/AZURE-ARCHITECTURE.md section 12): the worker submits a batch job
-- and leaves. This row is everything a later worker needs to collect the result, so a restart or a
-- second replica never submits (and pays for) the same audio twice. One row per waiting meeting.

CREATE TABLE speech_jobs (
    workspace_id text        NOT NULL,
    meeting_id   text        NOT NULL,
    handle       text        NOT NULL,
    submitted_at timestamptz NOT NULL,
    polls        integer     NOT NULL DEFAULT 0,
    PRIMARY KEY (workspace_id, meeting_id)
);

ALTER TABLE speech_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE speech_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON speech_jobs
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
