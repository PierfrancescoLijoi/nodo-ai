# Knowledge base Phase 7 — voice and local models

## Objective

Complete FR-20, FR-21, FR-22, FR-24 and FR-25 without weakening NODO's local-first or
evidence-only guarantees. Existing streaming meeting transport is retained; this phase adds the
owner-facing controls and a model lifecycle that is explicit, checksum-verified and removable.

## Current evidence

- `services/api/src/nodo_api/voice/` already implements session tickets, replay, buffered local
  recognition, streaming recognition, synthesis ports and meeting finalization.
- `apps/web/src/features/capture/CaptureScreen.tsx` already opens an attested live meeting, renders
  incremental finals and retains the upload fallback.
- `tools/local-setup/install_models.py` has the current install sources, published sizes and the
  SHA-256 values for direct downloads. It is a CLI-only workflow; it cannot yet satisfy FR-24.
- `/v1/local/status` reports missing runtime parts, but no catalog, install, remove or hardware
  recommendation endpoint exists.
- `/v1/voice-query` handles a text transcript. It does not yet accept push-to-talk PCM or return
  a local spoken-answer stream.

## Scope and non-goals

- All new state lives under the local engine model directory and is never synchronized.
- Model downloads remain opt-in. The API must receive an explicit, single-model confirmation;
  merely opening the catalog is never consent to download.
- Do not claim that a Hugging Face cache hash is a SHA-256. Catalog entries without an immutable
  SHA-256 must expose the pinned upstream revision and be unavailable for in-app installation until
  a content hash is supplied.
- Azure adapters retain their current gateway behaviour. No Azure deployment is required to close
  this phase.

## Implementation order

### Task 1 — Canonical catalog and machine profile

Create a typed local catalog shared by API and the installer. Each entry carries: stable id,
task, publisher, version/revision, bytes, SHA-256 or explicitly unsupported verifier, licence,
licence URL, hardware tier, installation paths and removable paths. Move direct-download metadata
out of `tools/local-setup/install_models.py` into that catalog.

Add a conservative hardware probe (`cpu`, `gpu_6gb`, `gpu_large`) that never calls the network.
It reports the detected profile plus the recommended model ids, not an automatic install.

Tests: catalog ids unique; direct artifacts all have 64-character hashes; discovery produces only
paths inside the model root; a CPU profile never recommends a GPU-only entry.

### Task 2 — Safe local model lifecycle API

Add local-only endpoints:

- `GET /v1/local/models`: catalog, installed state, byte totals and recommendation.
- `POST /v1/local/models/{id}/install`: body must contain `{ approved: true }`; creates one
  bounded job. The job downloads only the catalog URL, streams to a temporary file, verifies SHA-256
  before the atomic move and records progress.
- `GET /v1/local/models/{id}/install`: job status only, with errors redacted.
- `DELETE /v1/local/models/{id}`: requires the entry to be removable and no active job; erases only
  its catalog-owned relative paths after root containment checks.

The engine must refuse model lifecycle routes on memory/Azure backends. Add idempotency and
concurrency tests, checksum mismatch deletion tests and traversal tests.

### Task 3 — Models screen and first-run guidance

Add `/models` to `AppShell` and a local-only `ModelsScreen`. The screen presents size, licence,
checksum/revision verification, hardware fit, disk usage and installed/available state. A download
uses `ConfirmDialog` immediately before `approved: true`; removal uses a separate confirm dialog.
The first-run missing-model state links here from Settings and Capture. No button becomes enabled
for a non-verifiable artifact.

Tests: repository request contracts; confirmation before install/remove; locale coverage; keyboard
operation and an axe check.

### Task 4 — Push-to-talk dictation

Extend the voice session protocol with a `dictation` mode that owns no Meeting. It uses the existing
`BufferedRecognizer` on local engines, has a 120-second cap, and emits one final transcript on key
release. Add an authenticated REST session opener and WebSocket event shape rather than uploading
audio to a third party.

In `AskScreen`, add a hold-to-talk control that appends the final transcript to the editable input;
the user still presses Send. The control is unavailable with a clear status when local speech is
missing. Provide keyboard press-and-hold semantics and cancellation on Escape.

### Task 5 — Ask during live capture and spoken answers

Add a live-meeting Ask action that sends the finalized transcript evidence collected so far through
the existing query path and visibly marks provisional answers. It must never answer from unfinalized
audio.

Add a local `SpeechSynthesizer` adapter based on the selected permissively licensed catalog voice.
Expose a per-answer Speak/Stop action in Ask; it is manual-only and never autoplays. Stream PCM to
an `AudioContext`, cancel on Stop/navigation and show the voice licence in the Models screen.

### Task 6 — verification and documentation

Run API contract/security tests, web unit/type/lint/build, model lifecycle tests, capture/dictation
browser journeys and axe on Capture, Ask and Models. Record only measured voice/model performance
in `docs/PERFORMANCE.md`; do not extrapolate from the scripted recognizer.

## Delivery gates

1. A missing model cannot be downloaded without an explicit confirmation and a verified hash.
2. A failed checksum leaves no model artifact in the install location.
3. A removal affects only model-root paths owned by the selected catalog entry.
4. Dictation and spoken answers work without outbound application requests.
5. Live Ask uses only finalized evidence and retains normal citations/answerability.
6. All new UI controls have an accessible keyboard path and translated labels.
