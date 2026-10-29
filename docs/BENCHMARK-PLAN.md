# NODO AI - extensive performance and quality benchmark

Status: executable plan, 23 October 2026. The local synthetic smoke run described below has run;
Azure and the production local models have not. A result is publishable only when its corpus,
model/deployment version, machine/SKU, commit, seed and raw JSONL are retained together.

## 1. What this proves

The benchmark follows one recording from audio to a grounded answer:

1. upload and queue admission;
2. speech-to-text, language detection and speaker separation;
3. transcript persistence, embeddings and indexing;
4. retrieval, graph expansion and answer generation;
5. semantic-answer-cache hit, miss and invalidation;
6. deletion/ACL checks and system behavior under concurrency, spikes and dependency failures.

Azure and local runs use the same audio, references and JSON result schemas. Synthetic audio is a
repeatable regression fixture, not evidence of real-meeting quality.

## 2. Corpora and minimum sample sizes

### Synthetic regression corpus

For every scripted meeting generate: clean, white noise at 20/10/5 dB SNR, room echo, clipping,
120 ms dropouts, telephone band (300-3400 Hz), and 0 dB competing speech. The random seed is fixed.
Cover all four product languages when the matching voice packs exist, durations of 2, 15, 60 and
120 minutes, and 1, 2, 4 and 8 speakers. Run every provider/model three times to expose variance.

### Real-meeting certification corpus

At least 30 consented recordings per language (120 total), stratified across quiet rooms, open
offices, remote-call audio, phone-on-table audio and overlapping speech. Include the same duration
and speaker-count buckets, multiple microphones, accents and a balanced range of voices. A native
speaker corrects the transcript and speaker intervals. Keep a locked 20% test split that is never
used for threshold/model selection.

No production claim is allowed from fewer than 30 independent recordings per reported slice.
Report 95% bootstrap confidence intervals and both micro and macro averages so one long meeting
cannot hide failures in short meetings.

## 3. Metrics and release gates

| Stage | Metric | Beta gate |
|---|---|---|
| Transcription, clean real speech | micro/macro WER, CER | WER <= 8% per language |
| Transcription, all real meetings | micro/macro WER | WER <= 15% per language; upper CI <= 18% |
| Critical content | named-entity recall, number/date recall | >= 95% / >= 98% |
| Speaker separation | speaking-time attribution after optimal label mapping | >= 90%; also report speaker-count error |
| Language identification | meeting locale accuracy | >= 98%; code-switching reported separately |
| Local speech speed | real-time factor p50/p95 | p95 <= 0.417 CPU, <= 0.033 GPU |
| End-to-end 60 minute meeting | upload-complete to review | local p95 <= 20 min; Azure p95 <= 15 min |
| Retrieval | citation precision / recall | >= 95% / >= 90% |
| Answerability | correct supported/partial/unanswerable class | >= 95% |
| Isolation/deletion | forbidden citation or content leaks | exactly 0 |
| Query latency | p95, model warm | <= 5 s local; <= 6 s Azure |
| API reads | p95 at 50 concurrent users | <= 800 ms externally; server-side target <= 100 ms |
| Cache | hit correctness and stale-hit count | 100% correct hits; zero stale hits; report hit rate and saved latency/tokens |
| Reliability | failed jobs and duplicate provider submissions | < 0.1%; duplicate billable submissions = 0 except the documented submit/store crash window |

`speaker_accuracy` is deliberately not DER: it measures the share of true speaking time assigned to
the right anonymous speaker. If false alarms and missed speech matter for a provider decision, add
standard DER/JER using the same intervals.

## 4. Executable local speech workflow

```powershell
# Select installed Windows voices. Speaker B is pitch shifted so it is audibly distinct.
python tools/speech-bench/make_corpus.py tmp/speech-clean --languages en,it

# Produce deterministic adverse meeting conditions.
python tools/speech-bench/stress_corpus.py tmp/speech-clean tmp/speech-stress

# Run the exact local model/configuration under test and retain raw JSONL.
python tools/speech-bench/bench_whisper.py medium cuda float16 tmp/speech-stress 0 tmp/results/local-medium.jsonl

# Produce the quality gate, slices and 95% confidence interval.
python tools/speech-bench/quality_report.py tmp/results/local-medium.jsonl tmp/results/local-medium-report.json 30

# Run speaker separation independently, then the app's real HTTP path.
python tools/speech-bench/bench_diarization.py tmp/speech-clean
python tools/local-check/e2e_http.py tmp/speech-clean/en.wav en
```

Repeat `e2e_http.py` for every base recording and retain state-transition time, worst `/healthz`
latency, extracted people/decisions/actions and query latency. Run a 60-minute case while issuing a
probe every 500 ms, then an 8-hour soak with meetings submitted at the expected peak arrival rate.

## 5. Executable Azure speech workflow

Upload the identical WAV files to the private benchmark container, create read-only one-hour SAS
URLs, and run through the production adapter:

```powershell
$env:NODO_SPEECH_ENDPOINT = "https://<speech>.cognitiveservices.azure.com"
python tools/speech-bench/bench_azure.py urls.json tmp/speech-stress tmp/results/azure-speech.jsonl
python tools/speech-bench/quality_report.py tmp/results/azure-speech.jsonl tmp/results/azure-speech-report.json 30
```

Run once per candidate locale/model configuration. Capture Speech request/job ids, queue wait,
provider processing time, poll count, throttles, retries and cost per audio hour. Preserve the raw
JSONL and telemetry in CI as benchmark artifacts.

## 6. Retrieval and answer benchmark

The golden set has 60 balanced cases (10 categories of 6) over facts, temporal changes, multilingual questions,
ambiguity, entity collisions, cross-project false joins, ACL negatives and deleted data.
For each backend produce one prediction per case:

```json
{"id":"case-id","answerability":"supported","citation_segment_ids":["segment-id"],"latency_ms":1234}
```

Score local and Azure identically:

```powershell
python eval/validate_golden_set.py
python eval/score_results.py eval/golden_set/cases.jsonl tmp/results/local-query.jsonl tmp/results/local-query-report.json
python eval/score_results.py eval/golden_set/cases.jsonl tmp/results/azure-query.jsonl tmp/results/azure-query-report.json
```

Run four passes: cold process/cache, warm process with empty answer cache, exact repeat, and semantic
paraphrase. Then mutate a transcript, add a meeting, approve a proposal and delete a cited meeting;
every following query must miss the old answer and return no stale quote. Run the same questions
from another owner/workspace and require zero forbidden citations.

## 7. Load, spike, soak and failure matrix

For both backends use the production request mix: 70% list/detail, 15% graph, 10% questions and 5%
meeting creation/status. Test 1, 16, 50, 100 and 250 concurrent users.

1. Warm-up: 15 minutes, discarded.
2. Step load: 15 minutes at each level; report p50/p95/p99, throughput and errors by endpoint.
3. Spike: 10x expected load for 5 minutes; verify bounded 429/503 with `Retry-After`, not timeouts.
4. Soak: 8 hours at expected peak; check memory, connections, queue age, model quota and cache size.
5. Speech burst: complete 100/1,000 meetings at the top of the hour; verify KEDA scaling and no
   duplicate provider jobs.
6. Failures: model 429/5xx, Search unavailable, Speech delayed, worker killed mid-job, PostgreSQL
   failover, queue dead-letter, primary-region loss and stale JWKS. Verify the documented degraded
   response and recovery target for each.

For Azure, run against the real production SKU after the first deployment and record Front Door,
APIM, Container Apps, PostgreSQL, Service Bus, Search, OpenAI and Speech telemetry. For local, run
with the desktop UI active on the target 6 GB GPU laptop and record CPU, RSS, VRAM, disk and UI/API
responsiveness.

## 8. Baseline executed on 23 October 2026

Environment: RTX 4050 Laptop GPU (6 GB), synthetic English meeting, Whisper `tiny.en` smoke model,
9 deterministic conditions, 0.264 audio hours. This is a harness validation, not the product model.

| Metric | Result |
|---|---|
| Clean WER | 3.0% |
| 5 dB noise WER | 12.45% |
| Competing speech WER | 65.67% |
| Telephone-band WER | 68.24% |
| All-condition micro WER | 19.03% (gate failed) |
| WER macro 95% bootstrap CI | 4.63%-39.15% |
| RTF p50 / p95 | 0.0275 / 0.0588 |
| Decode latency p50 / p95 | 2.904 s / 6.211 s |
| Entity recall | 63.25% |

The failure is useful: a clean-only average would have looked excellent and hidden two severe
conditions. The next valid comparison is the production `medium` model on this same corpus, then the
real multilingual certification corpus.

## 9. Evidence package

Every benchmark artifact directory contains: manifest and seed; WAV hashes; corrected references;
raw provider/app JSONL; aggregate JSON report; model/deployment version; git commit; hardware/SKU;
configuration excluding secrets; start/end timestamps; and relevant telemetry export. A run is
invalid if cases are missing, thresholds changed after seeing the test split, or raw results are not
available for recomputation.

## 10. Real-time voice sessions

The real-time design has a separate evidence package; see
[VOICE-REALTIME-ARCHITECTURE.md](VOICE-REALTIME-ARCHITECTURE.md). Generate session JSONL with frame
counts and correlated latency spans, then apply the transport-independent gate:

```powershell
python tools/voice-bench/session_metrics.py tmp/results/voice-sessions.jsonl tmp/results/voice-report.json 100 assistant
```

Test both WSS gateway and any optional WebRTC implementation with the same audio and metrics:

- 100, 500 and 1,000 concurrent sessions, ramped rather than opened in one burst;
- 2/15/60/120-minute sessions, including provider/Front Door rotation before hard limits;
- 0/1/3/5% packet loss, 20/80/200 ms jitter, reordering and constrained bandwidth;
- Wi-Fi/mobile handoff and 5/15/30-second disconnects with replay/resume;
- duplicate, missing and out-of-order sequence numbers and idempotent final transcripts;
- slow Speech callbacks, 429, token refresh, gateway kill, rollout drain and Redis loss;
- push-to-talk false activation, VAD pause boundaries, echo, simultaneous speech and barge-in;
- one tenant exhausting its quota while other tenants retain their reserved capacity;
- cross-workspace ticket replay and expired/revoked session tickets (must yield zero content leak).

Report partial/final/first-audio/reconnect/barge-in p50/p95/p99, expected/recovered frame ratio,
duplicate frame rate, success rate, active sessions per replica, CPU/RSS/network, downstream 429s
and cost per session-minute. A real-time result cannot pass with fewer than 100 independent sessions;
1,000 sessions is a capacity demonstration only after the needed Azure quotas are approved.


## 11. Measured with the production local models, 23 October 2026

Machine: the target laptop (RTX 4050, 6 GB). Models: Whisper `medium` float16, the E5 embedder and
the local language model the desktop app ships. Raw files and reports: `docs/benchmarks/2026-10-23/`.
This is the first run of the plan on real models; Azure has not run and the corpora are small.

### Speech to text (one synthetic English meeting, 9 audio conditions, 0.26 audio hours)

| Condition | WER |
|---|---|
| clean | 3.9% |
| noise 20 dB / 10 dB / 5 dB | 3.9% / 3.4% / 6.0% |
| echo / clipping / dropouts | 4.3% / 4.3% / 4.7% |
| **competing speech (0 dB babble)** | **39.9%** |
| **telephone band** | **67.8%** |
| all conditions, micro | 15.4% (95% CI of the macro mean 4.2% to 32.4%) |

Number recall 100%, entity recall 65%. RTF p50 0.058, p95 0.083 on single short files with the
model loaded; the batched figure on long audio in `PERFORMANCE.md` (0.02) is the one that applies
to a real meeting. VRAM 2.1 GB. The gate reports `insufficient_data`: one recording per condition,
the plan asks for 30.

Reading: ordinary room problems cost almost nothing (3 to 6%). Two conditions break it. Competing
speech repeats and drops phrases; the telephone-band filter used here removes whole sentences. The
telephone filter is harsh and synthetic, so this says "test with real call audio", not "calls fail".
Speaker separation and the other three languages were not measured: this machine has one English
voice. Whisper `tiny.en` (the smoke model Codex ran first) gave 65.7% and 68.2% on the same two
conditions, so the model size fixes babble partly and telephone not at all.

The local speaker worker now removes VAD-confirmed silence before ERes2Net diarization and maps
turns back to the original timeline. The available two-voice corpus retained 85.8%-86.8% of samples
(13.2%-14.2% removed). This is a measured input reduction, not yet a measured end-to-end speedup;
the production diarization models were not mounted for the rerun. The aligned benchmark command is
`python tools/speech-bench/bench_diarization.py <corpus> <models> <ffmpeg>` and reports both RTF and
speaker accuracy, so a faster run cannot pass by damaging attribution quality.

### Questions and citations (golden set: 60 cases, 10 categories, 6 meetings, local pipeline)

Search plus answer, graph empty, one pass per language, each case asked in that language.

| Language | Answerability | Citation precision | Citation recall | Cross-workspace or deleted leaks | p95 latency |
|---|---|---|---|---|---|
| en | 73% | 76% | 45% | 0 | 1.7 s |
| it | 82% | 89% | 53% | 0 | 1.8 s |
| pl | 68% | 79% | 42% | 0 | 1.9 s |
| da | 72% | 85% | 45% | 0 | 1.7 s |

- The gate (95% / 95% / 90%) fails on quality in every language. The security rows hold: the six
  ACL cases and the six deleted-data cases returned nothing forbidden in all four languages.
- Three citations pointed at the wrong person or project inside the caller's own workspace (it:
  case-037; pl: case-037 and case-044). That is a quality error (the "two Alex" and "Aurora against
  Borealis" cases), not a leak, and the scorer counts it in the same column; read the categories.
- Weakest categories (English): decisions changing over time 33%, incomplete references 33%, false
  cross-project joins 50%, entity collisions 67%. Single-meeting facts, ACL, deleted data and
  unanswerable questions: 100%.
- Not in this run: the graph (it adds the change-over-time context those categories need), the
  multi-hop retrieval, and the production Azure models.

### Answer cache (ADR-026), same run, second pass of the same questions

| Language | Questions that reached the model: first pass | second pass | Identical answers |
|---|---|---|---|
| en | 60 | 36 | 59 of 60 |
| it | 60 | 31 | 58 of 60 |
| pl | 60 | 40 | 60 of 60 |
| da | 60 | 36 | 58 of 60 |

These are the pre-optimization results. At that revision only complete answers with citations were
stored, so negative results asked the model again. In English, 35 of 60 repeated cases were
unanswerable; their median was 617.2 ms and 32 exceeded 500 ms, while cached supported answers had
a 12.8 ms median. The current implementation also caches an unanswerable result for the exact same
normalized question, never for a semantic neighbour, and invalidates it on every knowledge epoch
change. This removes those 35 repeat model calls by construction; the production-model latency row
must be re-run before reporting a new measured time. Exact-only behavior, tenant scope and
invalidation are covered by `services/api/tests/test_answer_cache.py` and the shared memory/SQLite/
PostgreSQL store contract.

### What follows

1. The retrieval and answer quality needs work before it is promised: start with the two weakest
   categories and test whether the graph context closes the gap, then re-run this set.
2. Re-run the speech corpus on real call audio and real multi-speaker meetings, and in the other
   three languages with installed voices, before claiming any WER.
3. Nothing here measures Azure. The same two scripts (`eval/run_queries.py`, `tools/speech-bench`)
   run against it with the settings in section 5, after the first deployment.
