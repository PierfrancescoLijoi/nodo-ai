# ADR-014: A desktop app with two interchangeable engines, Azure or this computer

Status: accepted (supersedes the phone-first plan of ADR-001 for the first release)

## Context
The owner wants NODO AI as a desktop app that can be configured either for Azure or for models running on the same computer, with the local engine offering the same features as Azure, and with the option to connect Azure later and have it receive what was done locally. The product already has a ports-and-adapters API (ADR-006), so the engine is a matter of which adapters are plugged in.

## Decision
**One API, three adapter sets.** `backend=memory` (tests), `backend=azure` (cloud), `backend=local` (this computer). Services, routes, validation and the graph logic are shared; only the adapters differ.

| Capability | Azure | Local | Difference to know |
|---|---|---|---|
| Data, audit, queue | PostgreSQL, Service Bus | SQLite file, a table and a worker thread | single user, one machine |
| Graph | PostgreSQL (ADR-012) | SQLite | same contract suite passes on both; local walks one query per node |
| Audio | Blob storage, SAS links | files, one-time upload links to the engine | same link contract |
| Search | AI Search: four language analyzers plus vectors | SQLite FTS5 (BM25) plus exact vector ranking, fused | no per-language stemming, a prefix rule and the vectors stand in |
| Embeddings | Azure OpenAI | multilingual E5 base as int8 ONNX, on the CPU (1.5 s to load, 144 sentences per second) | query and passage prefixes handled by the adapter; vectors are 0.98 cosine to the full model |
| Speech | Azure Speech batch: diarization, language switching | Whisper medium (faster-whisper) plus sherpa-onnx speaker separation | one language per recording; speaker quality is lower and unmeasured on real voices |
| Language model | Azure OpenAI chat | Qwen3 4B through llama.cpp | same adapters and prompts (llama.cpp speaks the OpenAI chat API); a smaller model, so weaker extraction, especially in Polish and Danish |
| Long meetings | one call | windows of about 3 500 tokens, a window the model fails on is halved and retried, results merged | slower, a little less coherent |
| Sign in | Entra External ID | one secret per launch, loopback only | |

**Same features, not same quality.** Every feature of the API works locally, including speaker naming, the review of graph relations, the change feed, erasure of audio, text, search and graph, and the attestation. Accuracy depends on the models and is measured, not promised (docs/PERFORMANCE.md).

**The shell.** `python -m nodo_api.desktop` starts the engine on 127.0.0.1 with a secret that is new at each start, serves the exported web app, and opens a Chromium app window (Edge, shipped with Windows 11, or Chrome) with its own profile. No Electron: nothing to download, one runtime, the window and engine share a lifecycle. The secret reaches the page in the URL fragment, which is not sent to any server. A second launch opens a window on the running engine. Tauri was excluded (needs Rust); Electron or pywebview remain options if a tray icon, auto-update or a native installer become requirements.

**Heavy work stays out of the engine's process.** Speaker separation runs in its own process: the native library holds Python's global lock (it froze every request for minutes) and a native crash must not take the engine with it. The search model avoids PyTorch and transformers altogether (their import chain crashed the process).

**The GPU is leased.** A laptop GPU holds one big model at a time, so speech and the language model take turns; the language model server stops after ten idle minutes. A question asked while a recording is being transcribed waits for the GPU.

**Models are never downloaded by the engine.** It reports what is missing (`GET /v1/local/status`); installing is a visible user action.

## Consequences
- Tests: the local backend runs on the same suites as the others (graph contract, HTTP flow, sync), with model-dependent tests skipped when the models are absent.
- The Python engine is the product's core on the desktop; a packaged installer (PyInstaller) is a later step. The engine itself needs no PyTorch (embeddings run as ONNX); only the CUDA runtime libraries for Whisper are still taken from it in the development setup.
- Windows only for now: process management uses `tasklist` and `taskkill`, the window finder knows Edge and Chrome paths.
- Local data lives in `%LOCALAPPDATA%\NODO` (database, audio, secrets, window profile). Deleting a meeting erases its audio, transcript, search rows and graph contribution.
