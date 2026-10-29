# ADR-013: Speakers are named by the owner, not recognised by voice

Status: accepted

## Context
Transcripts carry anonymous speaker labels (S1, S2) from diarization. The owner wants to say who is who. Recognising people by their voice across meetings would need voiceprints, which are biometric data under GDPR article 9 and would also put third parties (everyone who speaks) in a biometric database.

## Decision
- Diarization (Azure Speech batch) separates voices inside one recording only. It produces labels, never identities.
- The owner names a label per meeting (`PATCH /v1/meetings/{id}/speakers/{speaker_id}`). The mapping is stored on the meeting (`meetings.speaker_names`), is deleted with it, and is never shared between meetings.
- A name is untrusted text that later enters a model prompt: it is restricted to letters, digits, marks, spaces and a little punctuation, at most 80 characters and 5 words.
- Naming queues a `reanalyze` job. The extraction runs again with the names in the prompt, and every named speaker becomes a `person` in the graph with their own segments as evidence. Reviewed decisions survive (ADR-012).
- Possible later, still without voiceprints: suggest a name from what people say ("I'm Sara", "thanks Marco") and let the owner confirm.

## Consequences
- Naming effort repeats per meeting. A calendar or contact import can pre-fill candidates later.
- The graph can hold the same person under spellings from different meetings; merging is a review action (ADR-004), never automatic.
- Accuracy of "who spoke" is bounded by diarization quality, measured in `tools/speech-bench` and docs/PERFORMANCE.md.
