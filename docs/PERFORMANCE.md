# Performance and execution targets

The consolidated local KPI definitions, release gates and complete measured scorecard are in
[LOCAL-PERFORMANCE-SCORECARD.md](LOCAL-PERFORMANCE-SCORECARD.md). This file retains the underlying
experiment narrative and historical measurements.

Measured on 22 October 2026 on one developer laptop (AMD Ryzen 7 8845HS, 8 cores, 31 GB RAM, NVIDIA RTX 4050 6 GB, Windows 11, PostgreSQL 16 in a local Docker container). Every number below comes from a script in the repository, so it can be repeated. Numbers marked **not measured** need the Azure subscription and are targets, not results.

## 1. Speech to text

Corpus: `tools/speech-bench`. Two scripted meetings (English 106 s, Italian 116 s), 14 turns each, two speakers, rendered with the Windows voices installed on the machine. Speaker B is the same voice pitched down, because the machine has one voice per language and a foreign voice reading the text is unintelligible. **The audio is clean synthetic speech: expect real meetings to be worse.** There is no Polish or Danish voice on the machine, so those two languages are **not measured**.

Local Whisper (faster-whisper 1.2.1, model `medium`, beam 5, VAD on), word error rate without numbers:

| Setup | Real-time factor | 1 h of audio takes | WER en / it | Memory |
|---|---|---|---|---|
| GPU float16, one pass | 0.07 | 4.2 min | 2.7 % / 3.3 % | 2.1 GB VRAM |
| GPU int8_float16, one pass | 0.08 | 4.8 min | 2.7 % / 5.1 % | 1.1 GB VRAM |
| GPU float16, batched (8) | 0.02 | 72 s | 2.7 % / 3.3 % | 2.1 GB VRAM |
| CPU int8, 8 threads | 0.31 to 0.37 | 19 to 22 min | 2.7 % / 4.7 % | no GPU |

- Language identification on the first 30 s was right on both files once the audio was real speech of that language (en 1.00, it 0.999). The first run, with a foreign voice reading the text, guessed Slovenian at 0.41: **a wrong language guess is the failure to design for.** The app passes the meeting's locale, and Azure runs continuous language identification over the four candidates.
- White noise did not hurt: WER stayed within one point from clean down to 5 dB SNR.
- Another conversation of equal loudness under the speech (0 dB babble) took Italian from 3.3 % to 15.3 % and English from 2.7 % to 3.1 %. Overlapping speech is the realistic weak spot.
- A 10.6 minute recording took 10.9 s end to end on the GPU (real-time factor 0.017, WER 2.4 %), so speed is stable on longer audio.

What this means for Azure: the production path is Azure AI Speech batch transcription, not Whisper, so these numbers size a local fallback and set the bar, they do not predict Azure's accuracy. `tools/speech-bench/bench_azure.py` runs the same corpus and the same scoring through the production adapter. **Not run: no subscription.** Polish, Danish and diarization quality are measured only there.

## 2. Who is speaking

The app never identifies a person from the voice (ADR-013). Diarization gives anonymous labels inside one recording, the owner names them. The quality of the labels is measured by `speaker_accuracy` in `tools/speech-bench/score.py`: the share of real speaking time attributed to the right speaker after the best matching of labels.

Local separation (sherpa-onnx: pyannote segmentation 3.0 plus a speaker embedding model), two synthetic voices (the English and the Italian Windows voice reading the same scripts), `tools/speech-bench/bench_diarization.py`:

| Embedding model | English | Italian | Speakers counted right |
|---|---|---|---|
| 3D-Speaker ERes2Net (chosen) | 78 % | 80 % | yes, at every clustering threshold from 0.5 to 0.9 |
| NeMo TitaNet small | 78 % | 43 % | no |
| WeSpeaker CAM++ | 44 % | 51 % | no (2 to 6 extra speakers) |

The same voice pitched down (what the first corpus used) is not separated by any model (41 to 53 %): a speaker model listens to the voice, not to its pitch. **Synthetic voices are a weak test**: they are monotone and few. Treat 78 to 80 % as "works on clearly different voices", and measure real people before promising anything. Azure Speech's diarization is **not measured** (no subscription).

## 3. API and database

`services/api/scripts/bench_api.py`, in-memory adapters over real HTTP (uvicorn, one process, client threads in the same process, so throughput is a lower bound):

| Call | 1 client p50 / p95 | 16 clients p50 / p95 | throughput at 16 |
|---|---|---|---|
| GET /healthz | 1.9 / 2.7 ms | 28 / 34 ms | 380 req/s |
| GET /v1/meetings | 3.0 / 4.4 ms | 35 / 44 ms | 323 req/s |
| GET /v1/meetings/{id} | 2.4 / 3.2 ms | 35 / 48 ms | 324 req/s |
| GET /v1/graph/subgraph | 2.8 / 4.1 ms | 38 / 47 ms | 303 req/s |
| POST /v1/query | 2.8 / 3.8 ms | 37 / 52 ms | 300 req/s |

The framework, auth and validation cost about 2 to 3 ms per request; /healthz is as slow as the real calls at 16 clients, which shows the limit here is the Python process and the benchmark client sharing one core, not the application code.

PostgreSQL, 100 000 meetings (90 000 for the measured owner):

| Call | p50 / p95 |
|---|---|
| list a page of 20 (first, after a cursor, at the very end) | 5.3 to 6.3 / 7 to 8 ms |
| get one meeting | 5.3 / 8.3 ms |
| list every meeting id of the owner (`owned_ids`) | **127 / 144 ms** |

Graph (ADR-012), 1 000 000 relations, 200 000 entities: two hops from the biggest hub 13 / 16 ms, substring search 34 / 42 ms, index-only scans on one partition.

### Found and fixed in this round
1. **Access filter grew with the history.** Every question loaded all the owner's meeting ids (127 ms at 90 000 meetings) and put them in the Search filter. The index now has an `owner_id` field and the filter is two equality clauses. Each cited meeting is still checked individually (a handful of lookups).
2. **One embedding call and one upload per sentence.** A one hour meeting has several hundred segments. Indexing now embeds and uploads 256 at a time.

### Known limits, not fixed
- Naming a speaker queues a full re-extraction (one LLM call over the whole transcript). Naming four speakers one after the other costs four calls. Debouncing (a delayed message) would coalesce them.
- Extraction sends the whole transcript in one request. A one hour meeting is roughly 12 000 tokens (an estimate, not measured), well inside a 128k window; the practical limit is the cap of 60 entities and 120 relations per meeting, which a very long meeting would reach. Chunking is not built.
- A single Python process saturates one core at roughly 300 requests per second. Container Apps scales replicas on request count; each replica holds a small Postgres pool (5), so the pool and the Flexible Server connection limit must be sized together.

## 4. Mobile app

`expo export --platform android` (Hermes bytecode, production):

| | Before | After |
|---|---|---|
| JavaScript bundle | 9.3 MB | **3.2 MB** |

The barrel import `from 'phosphor-react-native'` pulled the whole icon library (3 000 icon files, every weight) into the bundle even though 25 icons are used; Metro cannot drop unused exports of that package. `src/ui/icons.ts` now re-exports the used icons from their own files (the package documents `phosphor-react-native/src/icons/*` for this), and `src/ui/phosphor-svg.d.ts` adds the `className` prop type those source files need. Add new icons in `icons.ts`, not from the package root.

Not measured: cold start time and memory on a device (needs a phone or emulator; the Android build has not been made yet), and list scrolling on long histories (the meetings list is paged by cursor, 20 per page).

## 5. Execution targets

| Area | Target | Status |
|---|---|---|
| API read (list, detail, graph), server side | p95 ≤ 100 ms at 50 concurrent users | holds with a wide margin on the measured parts |
| Graph, 2 hops | p95 ≤ 50 ms up to 1 M relations per owner | measured 16 ms |
| Meetings list page | p95 ≤ 50 ms at 100 000 meetings | measured 8 ms |
| Question to answer | p95 ≤ 6 s | **not measured** (embedding + search + graph are small; the model call dominates) |
| Upload finished to reviewable, 60 min recording | p95 ≤ 15 min | **not measured** (Azure batch transcription dominates; our own steps are index, store and one extraction call) |
| Transcription error, clean speech | WER ≤ 8 % per language | en/it measured 3 to 5 % on synthetic audio; pl/da **not measured** |
| Transcription error, real meetings | WER ≤ 15 % | **not measured**; needs real recordings |
| Speaker attribution, two speakers | ≥ 90 % of speaking time | **not measured** |
| Language identification | ≥ 98 % of meetings get the right language | **not measured** on Azure |
| Local fallback (offline or no quota) | GPU batched ≤ 2 min per hour of audio; CPU ≤ 25 min per hour | measured 72 s and 22 min |

## 6. What would make these numbers real

1. Record two real conversations (one you and a second person, in Italian or English, with a phone on a table) and add them next to the synthetic ones: noise, interruptions and room echo are what separates 3 % from 15 %.
2. After the first deployment run `bench_azure.py` on the corpus and fill the **not measured** rows, including Polish and Danish.
3. Repeat `bench_api.py` against the Container App with real latency to the database.

## 7. Local engine (the desktop app, ADR-014)

Same laptop. The recordings go through the real engine over HTTP, as the app does it (`tools/local-check/e2e_http.py`; WebM/Opus files like a browser makes). Models: Whisper medium on the GPU, ERes2Net on 8 CPU threads in a separate process, E5 base (int8 ONNX) on the CPU, optional Horizon multilingual reranker-small (int8 ONNX) on the CPU, Qwen3 4B Q5_K_M on the GPU through llama.cpp (context 16 384).

| Recording | Upload to reviewable | speech, speakers and index | extraction | One question |
|---|---|---|---|---|
| 2 min, Italian (first run, models cold) | 76 s | 55 s | 21 s | 2.2 s |
| 9 min, English (first run) | 127 s | 80 s | 47 s | 2.7 s |
| **60 min, English (34 copies of the test meeting)** | **about 15 min** | **534 s** | **356 s** (about ten windows, adaptive) | 3.5 s |

Where the speech time goes, 3 595 s of audio (`tools/local-check/profile_speech.py`):

| Step | Time | Real-time factor |
|---|---|---|
| decode (ffmpeg) | 5 s | 0.001 |
| load Whisper (once per start) | 9 s | |
| language detection | 0.4 s | |
| transcription with word times (batched, GPU) | 61 s | 0.017 |
| **speaker separation (CPU, 8 threads, before VAD compaction)** | **477 s** | **0.13** |
| assemble phrases | 5 s | |

Speaker separation is the cost and it runs **in parallel** with the transcription, so the speech
step takes as long as the separation. The production worker now applies Silero VAD first, compacts
only confirmed speech, and restores every speaker turn to the original timestamp. On the two tightly
edited synthetic meetings this removes 13.2%-14.2% of samples; real meetings with longer pauses
should save more, but that is not yet a measured claim. The full ERes2Net timing must be rerun with
`tools/speech-bench/bench_diarization.py`; the script now exercises the exact production model,
thread count, VAD and timestamp-restoration path instead of the old CAM++/4-thread experiment. E5
as int8 ONNX loads in 1.5 s and embeds 144 sentences per second.

**Engine responsiveness.** While a one hour recording was processed, a probe request every 0.5 s took at most 1.4 s over 2 038 probes. It was not like that at first: the native separation library holds Python's global lock, which froze every request for the length of the call, and a crash in native code takes the whole process with it. The separation now runs in its own process (`diarize_worker.py`). The search model's import chain (PyTorch, transformers, pandas, pyarrow) crashed the engine twice with an access violation inside pyarrow; the engine no longer imports any of it.

**A failure the test found.** The first one hour run failed after five attempts: the language model's answer was cut off in dense windows (the output limit) and the extraction was all or nothing. A window that cannot be extracted is now cut in halves and retried (up to three times), a stretch that still fails is skipped with a warning, and only a transcript with nothing extractable fails the job. Windows are 3 500 tokens, the output limit 4 096.

**Targets for the local engine on a laptop with a 6 GB GPU**

| Area | Target | Status |
|---|---|---|
| One hour meeting, upload to reviewable | ≤ 20 min | **measured about 15 min** |
| Engine stays responsive while processing | slowest trivial request ≤ 2 s | **measured 1.4 s** |
| Question to answer, model warm | ≤ 5 s | measured 2.2 to 3.9 s |
| Question after 10 idle minutes | ≤ 30 s | the model reloads; **not measured** |
| GPU memory | ≤ 5 GB at any moment | the lease keeps one model on the GPU; speech 2.1 GB, language model about 4 GB with a 16k context |
| Quality | no claim | the 4B model misses tasks and owners and Whisper medium mishears product names ("Service Bus" as "Ashar Service Bus"); everything it proposes waits for approval |

What the local engine cannot do at all: switch language inside one recording; use more than one GPU model at once on a 6 GB card.
