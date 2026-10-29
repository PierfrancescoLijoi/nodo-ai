# NODO AI — local performance and quality scorecard

Status: baseline and release gates, 24 October 2026. This document covers only the local backend.
Measured values come from the versioned artifacts in `docs/benchmarks/2026-10-23/` and the runs
documented in `PERFORMANCE.md`. A target is not a measured result.

Test machine: Windows 11, AMD Ryzen 7 8845HS (8 cores), 31 GB RAM, NVIDIA RTX 4050 Laptop GPU
(6 GB). Main models: faster-whisper `medium` float16, ERes2Net diarization on 8 CPU threads, E5
base int8 ONNX embeddings and Qwen3 4B Q5_K_M through llama.cpp.

## 1. Status convention

| Status | Meaning |
|---|---|
| **PASS** | The measured value meets the gate in the tested scenario. It does not generalize beyond that scenario. |
| **FAIL** | The measured value misses the gate and requires improvement. |
| **INSUFFICIENT_DATA** | A value exists, but sample size, language or recording diversity is too small for a release claim. |
| **NOT_MEASURED** | No trustworthy observation exists yet. |

Percentiles use nearest-rank selection. Release-quality accuracy requires at least 30 independent
real recordings per reported language/scenario slice and a 95% bootstrap confidence interval.

## 2. Executive scorecard

| Area | Primary KPI | Local baseline | Release gate | Status | Decision |
|---|---|---:|---:|---|---|
| Clean transcription | WER, English synthetic | 3.86% | <= 8% per language | **INSUFFICIENT_DATA** | Accuracy is promising; collect real EN/IT/PL/DA meetings. |
| Adverse transcription | WER, all 9 synthetic conditions | 15.36%; CI 4.15%-32.43% | <= 15%; upper CI <= 18% | **INSUFFICIENT_DATA** | Telephone and overlap must be fixed or explicitly unsupported. |
| Long-audio STT speed | GPU batched RTF | 0.017-0.020 | p95 <= 0.033 | **PASS** | Whisper is not the local bottleneck on long audio. |
| Speaker attribution | Speaking-time accuracy, ERes2Net | 78% EN / 80% IT | >= 90% | **FAIL** | Do not promise speaker accuracy yet. |
| Meeting processing | 60 min audio to reviewable | about 15 min, one run | p95 <= 20 min | **INSUFFICIENT_DATA** | Speed meets the directional target; repeat enough runs for p95. |
| Retrieval correctness | Answerability accuracy | 68.3%-81.7% by language | >= 95% | **FAIL** | Retrieval/graph context is the highest-priority quality problem. |
| Citation quality | Precision / recall | 75.7%-89.2% / 41.9%-53.2% | >= 95% / >= 90% | **FAIL** | Recall is the largest quality gap. |
| Query latency | p95, warm local model | 1.672-1.925 s | <= 5 s | **PASS** | Preserve latency while improving retrieval quality. |
| Isolation | ACL/deleted-data categories | 100% answerability in all 4 languages; no cross-workspace/deleted citation observed | zero forbidden content | **PASS** | Keep as a blocking, zero-tolerance gate. |
| API overhead | p95, 16 local clients | 34-52 ms | server p95 <= 100 ms at 50 clients | **INSUFFICIENT_DATA** | Good baseline, but 50-client run is still required. |
| Graph traversal | 2 hops, 1 M relations | p95 16 ms | <= 50 ms | **PASS** | Graph storage is not currently the bottleneck. |
| Engine responsiveness | Worst probe during 1 h processing | 1.4 s | <= 2 s | **PASS** | Separate diarization process prevents engine freezes. |
| GPU memory | Peak active model | 2.1 GB STT; about 4 GB LLM | <= 5 GB | **PASS** | GPU lease must continue to prevent simultaneous residency. |
| Reliability | 8 h soak, peak submission rate | not run | < 0.1% failed jobs; zero duplicate billable work | **NOT_MEASURED** | Required before a production-local claim. |

## 3. Metric definitions

| Metric | Definition | Direction | Guardrail |
|---|---|---|---|
| WER | `(substitutions + deletions + insertions) / reference words` | Lower | Report per language and acoustic condition; do not hide failures in a global average. |
| CER | Same edit-distance formula over characters | Lower | Useful for names and highly inflected languages; never substitute it silently for WER. |
| Entity recall | Reference named entities appearing correctly in the transcript / reference entities | Higher | Report exact and normalized matching separately when introduced. |
| Number/date recall | Reference numbers and dates preserved / reference numbers and dates | Higher | A wrong number is a critical-content failure even when WER passes. |
| RTF | Processing seconds / audio seconds | Lower | Report cold and warm model runs separately. `0.02` means 72 seconds per audio hour. |
| Speaker accuracy | True speaking time assigned to the correct anonymous speaker after optimal label mapping / total true speaking time | Higher | Also report speaker-count error, DER and JER; current baseline is not identity recognition. |
| Citation precision | Correct returned citations / all returned citations | Higher | Returning no evidence must not improve the metric. |
| Citation recall | Correct returned citations / all expected citations | Higher | Pair with precision; increasing top-k alone can game recall. |
| Answerability accuracy | Correct `supported` / `partial` / `unanswerable` class / all cases | Higher | Score by category and language, not only globally. |
| Forbidden citation hits | Returned segment ids intersecting a case's forbidden set | Lower, zero only | Distinguish security boundaries from same-workspace relevance errors. Both block release, but require different fixes. |
| Cache hit rate | Valid cache hits / eligible repeated questions | Higher | Correctness and zero stale hits override hit rate. Exact-negative and semantic-positive hits are separate metrics. |
| End-to-end latency | Reviewable timestamp - upload-complete timestamp | Lower | Include queueing, STT, diarization, indexing and extraction. |
| Availability | Successful operations / valid attempted operations | Higher | User cancellation and controlled admission rejection are separate outcomes. |

## 4. Transcription speed

### 4.1 Whisper configurations

| Configuration | RTF | Equivalent for 1 h audio | WER EN / IT | Memory | Interpretation |
|---|---:|---:|---:|---:|---|
| GPU float16, non-batched | 0.07 | 4.2 min | 2.7% / 3.3% | 2.1 GB VRAM | Accurate but unnecessarily slow for long recordings. |
| GPU int8_float16, non-batched | 0.08 | 4.8 min | 2.7% / 5.1% | 1.1 GB VRAM | Memory-saving fallback with an Italian quality cost. |
| GPU float16, batch 8 | 0.02 | 72 s | 2.7% / 3.3% | 2.1 GB VRAM | Selected long-audio path. |
| CPU int8, 8 threads | 0.31-0.37 | 19-22 min | 2.7% / 4.7% | CPU only | Passes the <=25 min/h fallback target. |
| Long recording, GPU batch | 0.017 | 10.9 s for 10.6 min | 2.4% EN | 2.1 GB VRAM | Shows stable long-audio throughput in one run. |
| Nine short stress files | p50 0.058; p95 0.083 | dominated by per-file overhead | see next table | 2.1 GB VRAM | Not comparable to one continuous 60-minute recording. |

### 4.2 Whisper medium accuracy by acoustic condition

Corpus: one synthetic English meeting, one observation per condition. Therefore every status below
is `INSUFFICIENT_DATA`, even where the point estimate passes.

| Condition | WER | Entity recall | Number recall | Gate interpretation |
|---|---:|---:|---:|---|
| Clean | 3.86% | 76.92% | 100% | WER passes; entity recall fails the 95% critical-content target. |
| White noise, 20 dB | 3.86% | 38.46% | 100% | Words pass, entity preservation fails. |
| White noise, 10 dB | 3.43% | 50.00% | 100% | Words pass, entity preservation fails. |
| White noise, 5 dB | 6.01% | 84.62% | 100% | WER passes; entity recall remains below target. |
| Echo | 4.29% | 92.31% | 100% | Close on entities, but below target. |
| Clipping | 4.29% | 92.31% | 100% | Close on entities, but below target. |
| 120 ms dropouts | 4.72% | 76.92% | 100% | General words survive better than names. |
| Competing speech, 0 dB | 39.91% | 53.85% | 100% | **Severe failure** on overlapping speech. |
| Telephone band | 67.81% | 19.23% | 100% | **Severe failure** on the synthetic telephone filter. |
| **All conditions** | **15.36%** | **64.96%** | **100%** | WER narrowly misses 15%; critical entity recall fails widely. |

Additional result: aggregate CER is 15.03%. English and Italian language identification was correct
on two clean recordings (probabilities 1.00 and 0.999), but this is not enough evidence for the 98%
gate. Polish, Danish, code-switching and real accents are not measured.

## 5. Diarization

| Model/scenario | EN speaker accuracy | IT speaker accuracy | Speaker count | Target | Status |
|---|---:|---:|---|---:|---|
| ERes2Net, two distinct synthetic voices | 78% | 80% | Correct across thresholds 0.5-0.9 | >= 90% | **FAIL** |
| NeMo TitaNet small | 78% | 43% | Incorrect | >= 90% | **FAIL** |
| WeSpeaker CAM++ | 44% | 51% | 2-6 extra speakers | >= 90% | **FAIL** |
| Same voice pitch-shifted | 41%-53% | 41%-53% | Unreliable | >= 90% | **FAIL** |
| Current ERes2Net after VAD compaction | not rerun | not rerun | not rerun | >= 90% | **NOT_MEASURED** |

The current worker removes 14.2% of samples on the EN fixture and 13.2% on IT before diarization.
That is a measured input reduction, not a measured latency improvement. The previous one-hour
speaker-separation time was 477 seconds (RTF 0.13). The mandatory rerun must report speed and
speaker accuracy together, so silence removal cannot pass by damaging attribution.

## 6. Complete meeting pipeline

| Recording | Upload to reviewable | Speech + speakers + index | Extraction | Warm question | Gate/status |
|---|---:|---:|---:|---:|---|
| 2 min IT, cold models | 76 s | 55 s | 21 s | 2.2 s | Functional sample only. |
| 9 min EN, cold models | 127 s | 80 s | 47 s | 2.7 s | Functional sample only. |
| 60 min EN synthetic | about 15 min | 534 s | 356 s | 3.5 s | Directionally <=20 min, but no p95 yet. |

One-hour stage profile: ffmpeg decode 5 s, Whisper load 9 s, language detection 0.4 s, batched
transcription 61 s, pre-VAD diarization 477 s and phrase assembly 5 s. Transcription and
diarization overlap; extraction begins after their result. The next optimization target is therefore
speaker separation first, extraction second—not Whisper.

## 7. Retrieval, grounding and answer quality

Golden set: 60 cases per language, ten categories of six. These runs used the local pipeline with
an empty graph projection, so they measure the current semantic retrieval/answer path rather than
the full intended GraphRAG path.

| Language | Citation precision | Citation recall | Answerability | Forbidden-set hits | Latency p50 / p95 / max | Gate |
|---|---:|---:|---:|---:|---:|---|
| English | 75.68% | 45.16% | 73.33% | 0 | 773 / 1,672 / 1,964 ms | **FAIL quality; PASS latency** |
| Italian | 89.19% | 53.23% | 81.67% | 1, entity collision | 828 / 1,806 / 3,476 ms | **FAIL quality; PASS latency** |
| Polish | 78.79% | 41.94% | 68.33% | 2, entity/project relevance | 695 / 1,925 / 3,042 ms | **FAIL quality; PASS latency** |
| Danish | 84.85% | 45.16% | 71.67% | 0 | 707 / 1,723 / 2,787 ms | **FAIL quality; PASS latency** |
| **Release gate** | **>=95%** | **>=90%** | **>=95%** | **0** | **p95 <=5,000 ms local** | Quality blocks release. |

`Forbidden-set hit` is the scorer's strict relevance/security guard. The observed Italian and
Polish hits are wrong same-workspace entity/project citations, not observed cross-workspace or
deleted-data disclosure. They still fail the release gate. ACL-negative and deleted-data
answerability was 100% in every language.

### 7.1 English accuracy by reasoning category

| Category | Accuracy | Gate | Priority |
|---|---:|---:|---|
| Single-meeting fact | 100% | 95% | Maintain. |
| ACL negative | 100% | 100% | Blocking guardrail; maintain. |
| Deleted data | 100% | 100% | Blocking guardrail; maintain. |
| Unanswerable | 100% | 95% | Maintain exact-negative cache semantics. |
| Speaker ambiguity | 83.33% | 95% | Improve speaker-aware evidence selection. |
| Entity collision | 66.67% | 95% | Improve entity disambiguation. |
| Multilingual/cross-lingual | 66.67% | 95% | Improve multilingual embedding/reranking. |
| False cross-project join | 50.00% | 95% | Add stronger project/meeting constraints. |
| Decision change over time | 33.33% | 95% | Feed temporal graph context into retrieval. |
| Incomplete reference | 33.33% | 95% | Add query rewriting/coreference resolution. |

The primary retrieval KPI is citation recall, with precision and zero forbidden citations as
guardrails. Raising top-k without reranking is not an acceptable fix because it can improve recall
while reducing precision and increasing wrong-project evidence.

### 7.2 Optional local reranker

The local search path now keeps BM25 + E5 + RRF as the candidate generator and can rerank only its
top 20 already ACL/project-filtered segments with `Horizon-Labs/multilingual-reranker-small`.
The model is Apache-2.0, 141M parameters and the quantized ONNX file is about 268 MB; the installer
pins revision `d35a2781eeb3de1d971ad328165277bdf5712e35`. It is
lazy-loaded, CPU-only, batched by eight, capped at 384 tokens per query/segment pair and fails back
to the original RRF order. It never retrieves or sees a segment outside the authorized candidate
set.

Measured on the target laptop on 24 October 2026, 20 short candidates, 100 warm iterations:

| Reranker metric | Result | Interpretation |
|---|---:|---|
| Cold load + first inference | 5,459 ms | Hide with `NODO_LOCAL_WARM_UP=true`; not part of warm query SLO. |
| Warm p50 | 247.68 ms | Acceptable second-stage cost. |
| Warm p95 | 302.37 ms | Leaves substantial room under the 5 s end-to-end query SLO. |
| Warm p99 | 386.54 ms | Bounded by the 20-candidate cap. |
| Warm mean | 255.15 ms | CPU-only, no contention with the GPU LLM/Whisper lease. |

An English gate-first A/B run then used the real E5 int8 embedder and Qwen3 4B answer model, with
the same 60 cases and a fresh runtime for each arm:

| English end-to-end | RRF baseline | RRF + reranker | Delta |
|---|---:|---:|---:|
| Citation precision | 91.18% | 60.38% | **-30.80 pp** |
| Citation recall | 50.00% | 51.61% | +1.61 pp |
| Answerability accuracy | 78.33% | 78.33% | 0 pp |
| Forbidden-set hits | 1 | 1 | 0 |
| Query p50 | 819.8 ms | 1,156.95 ms | +337.15 ms |
| Query p95 | 1,805.6 ms | 2,902.5 ms | +1,096.9 ms |

`Forbidden-set hit` includes a wrong same-workspace/project citation, not only a tenant disclosure;
ACL-negative and deleted-data cases remained 100% answerable in both arms. The reranker fails the
first language gate because the small recall gain is overwhelmed by the precision and latency
regressions. `NODO_LOCAL_RERANKER_ENABLED` therefore defaults to `false`; the implementation and
pinned model remain available for experiments. Italian, Polish and Danish were not promoted to the
certification stage after the English blocking failure. Raw predictions and reports are under
`tmp/results/` and the commands are in `eval/README.md`.

## 8. Answer cache

The table is the pre-optimization second pass. `Model calls saved` is `60 - second-pass calls`.

| Language | First-pass model calls | Second-pass calls | Calls saved | Saved share | Identical answers |
|---|---:|---:|---:|---:|---:|
| English | 60 | 36 | 24 | 40.0% | 59/60 |
| Italian | 60 | 31 | 29 | 48.3% | 58/60 |
| Polish | 60 | 40 | 20 | 33.3% | 60/60 |
| Danish | 60 | 36 | 24 | 40.0% | 58/60 |

English cached supported answers had a 12.8 ms median. Repeated unanswerable questions that were
not cached had a 617.2 ms median; 32 of 35 exceeded 500 ms. The current code also caches an exact
normalized unanswerable question and invalidates it on every knowledge-epoch change. This should
remove those 35 repeat model calls, but the optimized production-model pass has not been rerun.

Release gates: 100% correct hits, zero stale hits after create/update/delete/approval, zero
cross-workspace hits, and separate reporting for exact, semantic, negative and invalidated entries.

## 9. API, database and graph latency

### 9.1 API with in-memory adapters

| Endpoint | 1 client p50 / p95 | 16 clients p50 / p95 | Throughput at 16 |
|---|---:|---:|---:|
| `GET /healthz` | 1.9 / 2.7 ms | 28 / 34 ms | 380 req/s |
| `GET /v1/meetings` | 3.0 / 4.4 ms | 35 / 44 ms | 323 req/s |
| `GET /v1/meetings/{id}` | 2.4 / 3.2 ms | 35 / 48 ms | 324 req/s |
| `GET /v1/graph/subgraph` | 2.8 / 4.1 ms | 38 / 47 ms | 303 req/s |
| `POST /v1/query` without real model | 2.8 / 3.8 ms | 37 / 52 ms | 300 req/s |

This measures framework/auth/validation overhead, not model-backed query performance. The client
and server share the same process/core, so throughput is a conservative microbenchmark rather than
a capacity claim.

### 9.2 PostgreSQL and graph scale fixtures

| Operation | Dataset | p50 / p95 | Gate/status |
|---|---:|---:|---|
| List 20 meetings, beginning/middle/end | 100k meetings; 90k same owner | 5.3-6.3 / 7-8 ms | **PASS** <=50 ms. |
| Get one meeting | 100k meetings | 5.3 / 8.3 ms | **PASS** <=50 ms. |
| Load all owner ids, obsolete hot path | 90k ids | 127 / 144 ms | Removed from query path; retain as regression check. |
| Graph traversal, 2 hops from largest hub | 1 M relations, 200k entities | 13 / 16 ms | **PASS** <=50 ms. |
| Graph substring search | same graph fixture | 34 / 42 ms | **PASS** <=50 ms. |
| Embeddings, E5 base int8 ONNX | local batch | 144 sentences/s; 1.5 s load | No release gate yet. |

## 10. Resource use and operational behavior

| Metric | Baseline | Gate | Status |
|---|---:|---:|---|
| Whisper GPU memory | 2.1 GB float16; 1.1 GB int8_float16 | <=5 GB total active model | **PASS** |
| Qwen3 4B GPU memory | about 4 GB at 16k context | <=5 GB total active model | **PASS** |
| Concurrent GPU models | prevented by GPU lease | never exceed device capacity | **PASS by design**, add telemetry proof. |
| Engine probe while processing 1 h | max 1.4 s over 2,038 probes | <=2 s | **PASS** |
| Warm question latency | 2.2-3.9 s end-to-end examples | <=5 s | **PASS** |
| Question after 10 idle minutes | not measured | <=30 s | **NOT_MEASURED** |
| Desktop JavaScript bundle | 3.2 MB, down from 9.3 MB | Track, no release gate yet | Measured size only. |
| Cold desktop start / UI memory | not measured | Define after device baseline | **NOT_MEASURED** |
| 8 h memory/handle stability | not measured | no sustained growth; no crash | **NOT_MEASURED** |

## 11. Reliability and load matrix required for release

| Test | Workload | Required metrics | Pass condition |
|---|---|---|---|
| Repeated STT | 30 real meetings/language/scenario, three runs | WER/CER, entity/number recall, RTF p50/p95/p99, failures | Accuracy gates hold with 95% CI; failure rate <0.1%. |
| Speaker certification | 1/2/4/8 speakers, overlap buckets, 30 meetings/slice | DER, JER, speaker accuracy/count error | >=90% attribution and no systematic over-counting. |
| Duration scaling | 2/15/60/120 min | stage and E2E p50/p95/p99, peak RSS/VRAM | Near-linear scaling; 60 min p95 <=20 min. |
| Query quality | 60 cases x 4 languages x >=5 deterministic runs/model revision | citation P/R, answerability, forbidden hits, p95 | 95%/90%/95%, zero forbidden hits, p95 <=5 s. |
| Cache correctness | exact/paraphrase plus every knowledge mutation | hit class, stale hit, saved time/tokens | 100% correct, zero stale/cross-tenant hits. |
| API step load | 1/16/50/100/250 clients, 15 min/step | throughput, p50/p95/p99, errors, CPU/RSS | 50-client server p95 <=100 ms; bounded errors above capacity. |
| Spike | 10x expected load for 5 min | 429/503, timeout rate, recovery time | Controlled rejection with `Retry-After`; no corruption. |
| Soak | 8 h at expected peak with desktop UI active | RSS/VRAM/handles, queue age, failures | No unbounded growth; failed jobs <0.1%. |
| Fault injection | kill worker/diarizer/LLM, corrupt output, disk pressure | state recovery, retries, duplicate work | Transcript preserved; no duplicate billable work; visible terminal state. |
| Privacy | cross-workspace, deleted meetings, stale cache, crafted ids | forbidden citations/content | Exactly zero. |

## 12. Release decision

The current local backend is fast enough for an engineering beta on the measured laptop, but it is
not yet quality-certified. The release blockers are retrieval recall/answerability, real-world
speaker attribution, overlapping/telephone speech, multilingual real-meeting coverage and the
missing soak/fault evidence. Latency optimization must not take priority over those quality gaps.

The next publishable milestone is not a faster synthetic benchmark. It is a run containing at least
30 consented real recordings per language, corrected references, retained raw predictions and
confidence intervals, followed by the same locked evaluation after every model or retrieval change.
