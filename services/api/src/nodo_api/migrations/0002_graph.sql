-- Temporal knowledge graph (ADR-012). Entities, relations and the evidence behind every relation,
-- all hash-partitioned by workspace so a workspace's data lives in one partition, indexes stay small
-- and the same layout can later be distributed (Citus) or moved to a graph engine behind GraphStore.
-- Reads are always scoped by (workspace_id, owner_id), the leading columns of every key.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE meetings
    ADD COLUMN title       text,
    ADD COLUMN summary     text,
    ADD COLUMN duration_ms integer;

CREATE INDEX meetings_list_idx ON meetings (workspace_id, owner_id, created_at DESC, id DESC)
    WHERE tombstoned_at IS NULL;

DROP TABLE graph_nodes;

CREATE TABLE entities (
    workspace_id text        NOT NULL,
    owner_id     text        NOT NULL,
    id           uuid        NOT NULL,
    type         text        NOT NULL
        CHECK (type IN ('person', 'project', 'system', 'topic', 'decision', 'action', 'meeting')),
    label        text        NOT NULL CHECK (char_length(label) BETWEEN 1 AND 240),
    normalized   text        NOT NULL,
    status       text,
    effective_at timestamptz,
    due_at       timestamptz,
    ref          text,
    updated_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, owner_id, id)
) PARTITION BY HASH (workspace_id);

CREATE TABLE relations (
    workspace_id text        NOT NULL,
    owner_id     text        NOT NULL,
    id           uuid        NOT NULL,
    source_id    uuid        NOT NULL,
    target_id    uuid        NOT NULL,
    kind         text        NOT NULL
        CHECK (kind IN ('MENTIONED_IN', 'ABOUT', 'DECIDED_IN', 'PROPOSED_BY', 'ASSIGNED_TO',
                        'DEPENDS_ON', 'SUPERSEDES', 'CONTRADICTS')),
    status       text        NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    confidence   real        NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    valid_from   timestamptz,
    valid_to     timestamptz,
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, owner_id, id),
    CHECK (source_id <> target_id)
) PARTITION BY HASH (workspace_id);

CREATE TABLE relation_evidence (
    workspace_id text NOT NULL,
    owner_id     text NOT NULL,
    relation_id  uuid NOT NULL,
    meeting_id   text NOT NULL,
    segment_id   text NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, relation_id, meeting_id, segment_id),
    FOREIGN KEY (workspace_id, owner_id, relation_id)
        REFERENCES relations (workspace_id, owner_id, id) ON DELETE CASCADE
) PARTITION BY HASH (workspace_id);

DO $$
BEGIN
    FOR i IN 0..7 LOOP
        EXECUTE format('CREATE TABLE entities_p%s PARTITION OF entities FOR VALUES WITH (MODULUS 8, REMAINDER %s)', i, i);
        EXECUTE format('CREATE TABLE relations_p%s PARTITION OF relations FOR VALUES WITH (MODULUS 8, REMAINDER %s)', i, i);
        EXECUTE format('CREATE TABLE relation_evidence_p%s PARTITION OF relation_evidence FOR VALUES WITH (MODULUS 8, REMAINDER %s)', i, i);
    END LOOP;
END $$;

-- Traversal: both directions are index-only scans; rejected relations are not indexed at all.
-- The leading columns make every read a single-partition, single-owner range scan.
CREATE INDEX relations_out_idx ON relations (workspace_id, owner_id, source_id, confidence DESC, id)
    INCLUDE (target_id, kind, status, valid_from, valid_to) WHERE status <> 'rejected';
CREATE INDEX relations_in_idx ON relations (workspace_id, owner_id, target_id, confidence DESC, id)
    INCLUDE (source_id, kind, status, valid_from, valid_to) WHERE status <> 'rejected';
CREATE INDEX relations_changes_idx ON relations (workspace_id, owner_id, created_at DESC)
    WHERE kind = 'SUPERSEDES' AND status <> 'rejected';

-- Erasure and re-extraction find a meeting's evidence by meeting, not by relation.
CREATE INDEX evidence_meeting_idx ON relation_evidence (workspace_id, meeting_id);

-- Finding entities by what the user typed: exact and prefix through btree, substring through trigrams.
CREATE INDEX entities_normalized_idx ON entities (workspace_id, owner_id, normalized text_pattern_ops);
CREATE INDEX entities_trgm_idx ON entities USING gin (normalized gin_trgm_ops);
CREATE INDEX entities_recent_idx ON entities (workspace_id, owner_id, type, updated_at DESC);
CREATE INDEX entities_updated_idx ON entities (workspace_id, owner_id, updated_at DESC) WHERE type <> 'meeting';

ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE relation_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;
ALTER TABLE relations FORCE ROW LEVEL SECURITY;
ALTER TABLE relation_evidence FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON entities
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON relations
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY workspace_isolation ON relation_evidence
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
