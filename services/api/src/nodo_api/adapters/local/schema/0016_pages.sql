-- Authoritative human Wiki revisions and replaceable machine proposals (ADR-031).
-- Per-revision claims are retained so source deletion can redact all historic copies safely.
CREATE TABLE pages (
    workspace_id      TEXT NOT NULL,
    id                TEXT NOT NULL,
    owner_id          TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('concept', 'source_summary', 'saved_answer')),
    subject_id        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'stale')),
    current_revision  INTEGER,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id),
    CHECK ((status = 'proposed' AND current_revision IS NULL)
        OR (status IN ('approved', 'stale') AND current_revision >= 1))
);
CREATE INDEX pages_owner_created ON pages (workspace_id, owner_id, created_at DESC, id);

CREATE TABLE page_proposals (
    workspace_id   TEXT NOT NULL,
    page_id        TEXT NOT NULL,
    id             TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (workspace_id, page_id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, page_id) REFERENCES pages (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE page_revisions (
    workspace_id TEXT NOT NULL,
    page_id      TEXT NOT NULL,
    rev          INTEGER NOT NULL CHECK (rev >= 1),
    body_md      TEXT NOT NULL CHECK (length(body_md) > 0),
    author_id    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    model_version TEXT,
    prompt_version TEXT,
    PRIMARY KEY (workspace_id, page_id, rev),
    CHECK ((model_version IS NULL) = (prompt_version IS NULL)),
    FOREIGN KEY (workspace_id, page_id) REFERENCES pages (workspace_id, id) ON DELETE CASCADE
);

-- `proposal_id` NULL means an approved/human revision; non-NULL belongs to the pending proposal.
CREATE TABLE page_claims (
    workspace_id  TEXT NOT NULL,
    page_id       TEXT NOT NULL,
    proposal_id   TEXT,
    rev           INTEGER,
    ordinal       INTEGER NOT NULL CHECK (ordinal >= 0),
    span_start    INTEGER NOT NULL CHECK (span_start >= 0),
    span_end      INTEGER NOT NULL CHECK (span_end >= span_start),
    text          TEXT NOT NULL CHECK (length(text) > 0),
    text_hash     TEXT NOT NULL,
    machine_generated INTEGER NOT NULL CHECK (machine_generated IN (0, 1)),
    PRIMARY KEY (workspace_id, page_id, proposal_id, rev, ordinal),
    CHECK ((proposal_id IS NULL) != (rev IS NULL)),
    FOREIGN KEY (workspace_id, page_id) REFERENCES pages (workspace_id, id) ON DELETE CASCADE
);
CREATE TABLE page_citations (
    workspace_id   TEXT NOT NULL,
    page_id        TEXT NOT NULL,
    proposal_id    TEXT,
    rev            INTEGER,
    claim_ordinal  INTEGER NOT NULL CHECK (claim_ordinal >= 0),
    ordinal        INTEGER NOT NULL CHECK (ordinal >= 0),
    source_id      TEXT NOT NULL,
    fragment_id    TEXT NOT NULL,
    source_version INTEGER NOT NULL CHECK (source_version >= 1),
    locator        TEXT NOT NULL,
    quote          TEXT NOT NULL CHECK (length(quote) > 0),
    PRIMARY KEY (workspace_id, page_id, proposal_id, rev, claim_ordinal, ordinal),
    CHECK ((proposal_id IS NULL) != (rev IS NULL)),
    FOREIGN KEY (workspace_id, page_id) REFERENCES pages (workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX page_citations_source ON page_citations (workspace_id, source_id, page_id);
