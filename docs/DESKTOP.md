# NODO AI on the desktop

The app runs in its own window on Windows. It works in one of two ways, chosen on first start and changeable later:

- **On this computer.** Speech, search, graph and answers are produced by models on your machine. Nothing leaves it. Needs a GPU for comfortable speed (an NVIDIA card with 6 GB is enough; the CPU works but is slow).
- **Azure.** The app talks to your deployed NODO API (see [AZURE-ARCHITECTURE.md](AZURE-ARCHITECTURE.md)). Needs the deployment and an Entra sign in.

You can start local and connect Azure later: **Settings, Copy to Azure** sends your meetings to the cloud (see [ADR-015](adr/015-sync-local-to-azure.md)).

## Install and start (developer setup)

```bash
# 1. the engine
cd services/api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,azure,local-models]"

# 2. the models (shows sizes and asks before downloading; about 5.5 GB in total)
.venv/Scripts/python ../../tools/local-setup/install_models.py
#    then, once, turn the search model into a small file the engine can run without PyTorch
.venv/Scripts/python ../../tools/local-setup/export_embedding_onnx.py

# 3. the interface, built once
cd ../../apps/mobile
npm ci
npm run export:desktop      # writes services/api/src/nodo_api/web

# 4. start
cd ../../services/api
.venv/Scripts/python -m nodo_api.desktop
```

Windows needs `ffmpeg` on the PATH (or `NODO_FFMPEG` set) to read recordings. On an NVIDIA card, CTranslate2 (Whisper) also needs the CUDA 12 runtime libraries (`cublas`, `cudnn`); in this development setup they come from a PyTorch installation.

Shortcut: `NODO.cmd` at the repository root starts the app.

`python -m nodo_api.desktop --no-window` runs only the engine (it prints its address and secret). A second launch opens another window on the engine that is already running.

## What is stored where (local mode)

`%LOCALAPPDATA%\NODO`

| Path | Content |
|---|---|
| `nodo.db` | meetings, transcripts, graph, search index, queue |
| `audio/` | the recordings, until you delete the meeting |
| `attestation.key` | signs the record that you declared you may record |
| `window/` | the app window's profile (microphone permission) |
| `models/` | llama.cpp, the speaker models and the search model (int8 ONNX, 280 MB); Whisper and the language model live in the Hugging Face cache |

Deleting a meeting removes its audio, transcript, search entries and graph entries from this computer, and from Azure on the next copy.

## What the models are good and bad at

Measured numbers are in [PERFORMANCE.md](PERFORMANCE.md). In short: transcription of clear speech is good in English and Italian; Polish and Danish are untested on real voices. The language model is small (4 billion parameters), so decisions and tasks it finds in a long meeting are less complete than Azure's, and it can attach the wrong name or miss a task. Everything it proposes is marked *proposed* and every answer cites the sentence it came from. A screen to approve or reject proposed decisions and tasks is not built yet (the API for it exists: `PATCH /v1/graph/relations/{id}`).

Speakers are separated by voice inside one recording and labelled S1, S2; you give them names (the app never recognises a person by voice across meetings).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "A local model needed for this request is not available" | a model is not installed: Settings shows which; run `install_models.py` |
| Questions are slow while a recording is processed | speech and the language model take turns on the GPU; wait for the recording to finish |
| First question after a pause takes about 20 seconds | the language model unloads after ten idle minutes and reloads |
| No microphone prompt | open Settings of the window's browser profile; the permission is stored in `window/` |
