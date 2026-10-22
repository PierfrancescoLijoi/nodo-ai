# NODO API v1 - contract for the app

The OpenAPI file in `packages/contracts/openapi.json` is generated from the code. This page is the human contract the mobile client is built against. All endpoints need `Authorization: Bearer <access token>` (Entra External ID). Errors use `{ "error": { "code", "message", "request_id" } }` with status 401, 404, 409 or 422. Timestamps are ISO 8601 UTC strings. A user only ever sees their own meetings and graph.

## Meetings

`GET /v1/meetings?limit=20&cursor=<opaque>` returns `{ items: MeetingListItem[], next_cursor: string | null }`, newest first. `limit` 1 to 100.

```
MeetingListItem {
  id, title, state: "uploading"|"transcribing"|"extracting"|"review"|"ready"|"failed",
  created_at, project: string|null, duration_ms: number|null, summary: string|null,
  people: string[], topics: string[], decision_count: number, action_count: number
}
```

`GET /v1/meetings/{id}` returns the meeting plus insights:

```
MeetingOutput {
  id, workspace_id, owner_id, title, state, created_at, project, duration_ms, summary,
  recording_attestation: {...},
  insights: {
    people:    { id, label }[],
    topics:    { id, label }[],
    decisions: { id, label, status: "proposed"|"approved"|"superseded", effective_at: string|null }[],
    actions:   { id, label, owner_label: string|null, due_at: string|null, status: "proposed"|"approved" }[]
  },
  speakers: { id: "S1"|"S2"|..., name: string|null, segments: number, talk_ms: number }[]
}
```

`speakers` is empty until the transcript exists. `name` is what the owner called that speaker, `null` if unnamed.

`PATCH /v1/meetings/{id}/speakers/{speaker_id}` with `{ name: string | null }` names a speaker (or clears the name with `null`) and returns the updated `MeetingOutput`. Rules: `speaker_id` matches `S<n>` and must exist in the transcript (404 otherwise); only meetings in `review` or `ready` can be edited (409); names are 1 to 80 characters, at most 5 words, letters, digits, marks, spaces, `.` `'` `-` (422 otherwise, no control characters). A change queues a background re-analysis, so the named person appears in the graph and in `insights.people` a few seconds later. Names belong to one meeting; nothing links them across meetings and no voice data is kept.

`POST /v1/meetings` accepts an optional `title` (1 to 120 characters). `GET /v1/meetings/{id}/transcript?limit&offset` is unchanged: items have `id, meeting_id, speaker_id ("S1", "S2"), start_ms, end_ms, text, confidence, version, locale`.

## Questions

`POST /v1/query` body `{ question, ui_locale, filters? }` returns `{ answer, answerability: "supported"|"partial"|"unanswerable", citations: { meeting_id, segment_id, speaker_id, start_ms, end_ms, quote }[], related_entities: string[], query_trace_id, model_version, retrieval_version }`.

## Graph

`GET /v1/graph/subgraph` with one of `seed_id` (entity id) or `entity` (text search on labels), or neither for an overview of recent entities. Other parameters: `depth` 1 to 2 (default 2), `include_proposed` (default false), `as_of` (ISO date, default now), `limit` 1 to 200 nodes (default 60). Returns:

```
Subgraph {
  nodes: { id, type: "person"|"project"|"system"|"topic"|"decision"|"action"|"meeting",
           label, status: string|null, meeting_id: string|null }[],
  edges: { id, source, target,
           kind: "MENTIONED_IN"|"ABOUT"|"DECIDED_IN"|"PROPOSED_BY"|"ASSIGNED_TO"|"DEPENDS_ON"|"SUPERSEDES"|"CONTRADICTS",
           status: "proposed"|"approved"|"rejected" (only returned by PATCH), valid_from: string|null, valid_to: string|null,
           confidence: number, evidence: { meeting_id, segment_id }[] }[],
  depth: number,
  truncated: boolean
}
```

An edge reads `source KIND target`, for example `decision-queue DECIDED_IN meeting-21-oct` or `decision-queue SUPERSEDES decision-sync`. `truncated` is true when a cap (nodes or per-node fan-out) hid part of the neighbourhood.

`PATCH /v1/graph/relations/{id}` body `{ "status": "approved" | "rejected" }` returns the edge (`rejected` edges disappear from later reads). Approving a `SUPERSEDES` edge marks the replaced decision `superseded`.

`GET /v1/graph/changes?limit=10` returns `{ items: { id, new_label, old_label, meeting_id, at }[] }`: the latest approved or proposed supersessions, newest first.
