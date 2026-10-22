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

## Recording lifecycle (all backends)

1. `POST /v1/meetings` with header `Idempotency-Key` (a fresh random string per recording, 8 to 128 characters) and body `{ recording_attestation: { notice_version: "2026-10-v1", locale: "en"|"pl"|"it"|"da", retention_policy_id: "retention-7d"|"retention-30d"|"retention-90d", acknowledged: true }, title?, project? }` returns the meeting (state `uploading`).
2. `POST /v1/meetings/{id}/upload-sessions` returns `{ id, upload_url, expires_in_seconds }`.
3. Send the audio to `upload_url` with `PUT`. **Local engine:** one request, raw bytes, no `Authorization` header (the link is the credential), answered `204`; a link works once. **Azure:** the link is a blob SAS URL: add header `x-ms-blob-type: BlockBlob` for one request up to 256 MiB, or stage blocks of 4 MiB with `PUT <url>&comp=block&blockid=<base64>` and finish with `PUT <url>&comp=blocklist` (body: `<BlockList><Latest>id</Latest>...</BlockList>`).
4. `POST /v1/meetings/{id}/complete` starts processing (state `transcribing`, then `extracting`, then `review`). Poll `GET /v1/meetings/{id}` every 3 seconds until the state is `review`, `ready` or `failed`.
5. `DELETE /v1/meetings/{id}` (204) erases audio, transcript, search rows and graph contribution.

## Local engine only (`/v1/local/...`)

The app is served by the engine and receives the secret in the address fragment (`http://127.0.0.1:PORT/#token=...`). It stores the token for the tab (`sessionStorage`), removes the fragment from the address, and sends it as `Authorization: Bearer <token>` like any other token. API base URL = the page's own origin.

- `GET /v1/local/status` returns `{ backend: "local", missing: string[], pending_jobs: number, gpu: boolean }`. `missing` names models or tools that are not installed (for example `"language model (GGUF)"`); with something missing, questions answer `503 model_unavailable`.
- `POST /v1/local/sync` body `{ cloud_url, access_token, force? }` starts copying the archive to an Azure deployment (ADR-015) and answers `202` with the progress object. `cloud_url` is the cloud API address (`https://...`); `access_token` is an Entra access token for that API; `force` sends every meeting again. `422` if the address is not a plain https address.
- `GET /v1/local/sync` returns `{ state: "idle"|"running"|"done"|"failed", total, done, pushed, unchanged, deleted, failed: { meeting_id, reason }[], message }`. Poll every 2 seconds while `running`.

## Cloud only

`PUT /v1/sync/meetings/{id}` receives one meeting from a local engine (ADR-015). Used by the engine, not by the app.
