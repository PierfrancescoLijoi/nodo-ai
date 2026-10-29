-- Names the owner gave to the anonymous speaker labels of a meeting: {"S1": "Sara"}.
-- A label is only ever an alias for this meeting; no voice data is stored (ADR-013).
ALTER TABLE meetings ADD COLUMN speaker_names jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(speaker_names) = 'object');
