-- Imported audio keeps its original only for a while (FR-5, the meeting rule of ADR-019): the
-- retention sweep erases the stored file once retention_until has passed and then clears the
-- column. NULL keeps the original until the source is deleted (every other kind). Fragments,
-- index and graph stay. Timestamps are fixed-width UTC text, so text order is time order.
ALTER TABLE sources ADD COLUMN retention_until TEXT;
-- A tiny persisted two-phase claim makes erase + audit crash-safe and prevents a concurrent
-- reimport from putting a new original underneath the retention worker.
ALTER TABLE sources ADD COLUMN retention_erase_phase TEXT;
CREATE INDEX sources_retention_idx ON sources (retention_until)
    WHERE retention_until IS NOT NULL AND tombstoned_at IS NULL;
