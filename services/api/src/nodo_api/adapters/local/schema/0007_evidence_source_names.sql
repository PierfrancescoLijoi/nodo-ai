-- Evidence points at a source and one of its fragments (ADR-029); a meeting is a source whose id is
-- the meeting id and a segment a fragment whose id is the segment id, so no value changes. The
-- rename follows the primary key, the index and nothing else: no view or trigger names these columns.
ALTER TABLE relation_evidence RENAME COLUMN meeting_id TO source_id;
ALTER TABLE relation_evidence RENAME COLUMN segment_id TO fragment_id;
