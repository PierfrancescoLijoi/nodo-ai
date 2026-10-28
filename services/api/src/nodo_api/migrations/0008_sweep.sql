-- The sweep (ADR-022) finds jobs the outbox recorded and nobody sent, and meetings whose processing
-- job began long ago and never finished. Both need to look at the job table across workspaces;
-- that is all this policy adds to the maintenance role of migration 0007. The table holds job
-- identities and ids (meeting id, export id), never content.

CREATE POLICY maintenance_scan ON jobs FOR SELECT TO nodo_maintenance USING (true);
