# ADR-002: Event-driven ingestion, preview separate from final transcript

Status: accepted

## Context
Transcription, extraction and indexing are slow, bursty and fail independently.

## Decision
Upload completion publishes one job to a queue. Workers are idempotent (`source_hash + pipeline_version + event_id`) with retries, dead-letter queue and per-workspace concurrency caps. Any live preview transcript is advisory; the batch transcript is the source of truth.

## Consequences
- Scale is a function of queue depth, not API size.
- Replays are safe, so upgrades re-run the pipeline.
- Users see state transitions (uploading, transcribing, extracting, review, ready, failed).
