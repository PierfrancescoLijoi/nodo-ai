-- Finding entities by meaning (ADR-024). One vector per entity label, so a question in any of the four
-- languages can start its walk over the graph at the entity it means, not only at one whose words it
-- shares. The column has no fixed size on purpose: the embedding model can change, and an entity
-- whose vector has another size is simply listed again for indexing. The search is exact within one
-- owner (workspace and owner lead the primary key), which stays fast for tens of thousands of
-- entities; an approximate index would need a fixed size and is not worth it at this scale.
--
-- The foreign key removes a vector with its entity, so erasing a meeting leaves nothing behind.

-- The extension is allowed by the server (azure.extensions) and created here, once, by the administrator
-- identity that runs the migrations.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE entity_vectors (
    workspace_id text    NOT NULL,
    owner_id     text    NOT NULL,
    entity_id    uuid    NOT NULL,
    dims         integer NOT NULL,
    vector       vector  NOT NULL,
    PRIMARY KEY (workspace_id, owner_id, entity_id),
    FOREIGN KEY (workspace_id, owner_id, entity_id)
        REFERENCES entities (workspace_id, owner_id, id) ON DELETE CASCADE
);

ALTER TABLE entity_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_vectors FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON entity_vectors
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
