# ADR-020: Speech to text runs in stages; a worker never waits for the provider

Status: accepted. Implemented and tested (`services/ingestion.py`, `adapters/azure/speech.py`, `speech_jobs_pg.py`, `servicebus.py`, migration 0006); the Azure calls are tested against the documented request shapes only, never against the live service.

## Context
Batch transcription takes minutes. The first version submitted the job and polled inside the worker until it finished. That held a Container Apps execution for the whole wait (the capacity model counted several times the executions needed for the real work), tied up a replica that could have processed other recordings, and meant that a crash or a restart re-submitted the audio, a second paid job for the same recording.

## Decision
Split the step into three calls on the provider and make the waiting a scheduled message instead of a loop.

1. **Submit.** The worker asks for a read-only link to the audio, submits the job, stores the provider's job handle in `speech_jobs` (one row per waiting meeting, workspace-scoped, forced row level security), schedules a `transcribe` message for 60 seconds later and returns. The message that brought it here completes.
2. **Poll.** When the scheduled message arrives (any worker, any replica), the service reads the handle and asks. Running: record the poll, schedule the next one with backoff (each wait half as long again, from 60 up to 300 seconds) and return. The key of every poll message is `meeting:poll:n`, so the platform's duplicate detection never mistakes one poll for another.
3. **Collect.** Succeeded: build the transcript from the phrases, save it as version 1, delete the provider job, forget the handle, and carry straight on to indexing and analysis in the same execution.

**Terminal outcomes do not retry.** A provider-reported failure, a result with no speech and a job older than the budget (two hours) cancel the provider job, clear the handle and mark the meeting failed; the message completes. Retrying would pay again for an answer that will not change. Errors that might be transient (the provider unreachable on a poll, the database down) still raise, so the existing delivery count retries them, up to five times, and then dead-letters and marks the meeting failed.

**Safe to repeat everywhere.** A transcript already saved short-circuits to the later stages and removes any job left behind by a crash between saving and cleaning up. A meeting deleted while it waits is found at the next poll message, which cancels the provider job and drops the handle instead of recreating anything.

**Safe to follow.** The handle is a URL that comes back from our own database and is followed with a bearer token, so the adapter refuses any handle that does not start with this resource's `/speechtotext/transcriptions/` address.

**The local engine is unchanged.** Whisper runs in-process and finishes in one call; staging is enabled only when the adapter, the job store and a queue that can schedule are all supplied.

## Alternatives considered
- **Webhook from the provider.** Batch transcription can call back. It would remove polling altogether, but needs a public endpoint to receive it, validation of the callback and a replay path; polling reuses the queue and the identities that already exist. Revisit if the poll executions dominate the cost.
- **Durable Functions or a workflow engine.** A good fit for long waits, and a new service to run, secure and monitor for one wait. Rejected for now.
- **Longer message locks and a worker that keeps polling.** Still occupies an execution per recording.
- **Store the handle on the meeting row.** One less table, but it widens the meeting model and every mapper for state that exists only while waiting.

## Consequences
- Each poll is a short execution of the worker job. The capacity model counts them (about seven per audio hour at the default backoff, both figures assumed); the knob is `NODO_SPEECH_POLL_SECONDS` and `NODO_SPEECH_POLL_MAX_SECONDS`: shorter is faster transcripts and more executions.
- Scheduled messages become active only when due, so the queue-length autoscaling rule starts the worker at the right time without any change.
- Open: a crash in the instant after the provider accepted a job and before the handle was stored submits once more (the provider removes the orphan after its six hour time to live); a meeting whose scheduled message is lost has no sweep to find it. Both are small and are listed in AZURE-ARCHITECTURE.md section 15.
- Time to a transcript is now measurable end to end (submit to collect), which unblocks the third objective in ADR-018.
