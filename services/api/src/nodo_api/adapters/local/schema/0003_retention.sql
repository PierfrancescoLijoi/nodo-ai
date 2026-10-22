-- When the audio of a meeting was erased because its retention period ended (ADR-019).
ALTER TABLE meetings ADD COLUMN audio_erased_at TEXT;
