# NODO AI - capacity model

Status: planning model, 23 October 2026. The inputs marked **measured** come from scripts in this repository; the ones marked **assumed** are planning values that a real subscription and real users must replace. Nothing here has run against Azure.

The question this answers is not "which SKU is big enough" but "what does one more person cost, in which resource, and when does each resource run out". The Terraform presets in `infra/terraform/envs/` are read off this model, and `python tools/capacity/model.py 25000` recomputes it for any size.

## 1. What the application does to Azure

A person produces three kinds of load, and they scale with different things:

| Load | Driver | Resource it lands on | Shape |
|---|---|---|---|
| Interactive requests (list, detail, graph, status polling) | daily active users | API replicas, PostgreSQL connections | bursty, working hours |
| Questions | daily active users x questions | model tokens (chat), Search queries, PostgreSQL reads | bursty, expensive per request |
| Recordings | hours of audio | storage, Speech, model tokens (extraction, embeddings), worker CPU, PostgreSQL writes, Search index size | spiky: meetings end together at the top of the hour |

The stock that grows without a ceiling is the **search index** (one vector per phrase) and **PostgreSQL**. Everything else is a rate.

## 2. Inputs

| Input | Value | Source |
|---|---|---|
| Audio per user per month | 10 hours | assumed |
| Daily active users | 30% of users | assumed |
| API requests per active user per day | 150 | assumed |
| Questions per active user per day | 3 | assumed |
| Peak hour carries | 15% of the day's requests; 10% of the day's audio | assumed |
| Burst inside the peak hour | 3x | assumed |
| Phrases per audio hour | 600 | measured (392 phrases in a 60 minute test meeting, rounded up) |
| Extraction tokens per audio hour | 16 000 in, 6 000 out | derived from the local windows (3 500 tokens + prompt) |
| Embedding tokens per audio hour | 25 000 | derived |
| Tokens per question | 3 000 in, 400 out | assumed |
| API throughput per vCPU | 150 requests/s, halved for headroom | measured by `services/api/scripts/bench_api.py` on a laptop, to be re-measured on the real SKU |
| Vector size | 512 dimensions, int8 | design choice (embedding models accept fewer dimensions; scalar quantization) |
| Index overhead | 1.3x | assumed |
| Speech wait per audio hour | 15 minutes | assumed, needs the subscription |
| Search partition | 160 GB, filled to 70% | assumed from the documented limits; confirm in the portal |

## 3. Results

Recomputed by `tools/capacity/model.py`:

| | 1 000 users | 10 000 users | 100 000 users |
|---|---|---|---|
| Daily active users | 300 | 3 000 | 30 000 |
| API peak requests per second | 5.6 | 56 | 562 |
| API replicas (2 vCPU, never below one per zone) | 3 | 3 | 3 |
| PostgreSQL connections from the API | 30 | 30 | 30 |
| Questions per minute at peak | 7 | 68 | 675 |
| Chat tokens per minute at peak | 23 k | 230 k | 2.3 M |
| Audio hours arriving in the peak hour | 33 | 333 | 3 333 |
| Extraction tokens per minute at peak | 12 k | 122 k | 1.2 M |
| Embedding tokens per minute at peak | 14 k | 139 k | 1.4 M |
| Worker executions at peak, job waits for speech | 9 | 89 | 889 |
| Worker executions at peak, staged jobs (polls included) | 2 | 19 | 186 |
| PostgreSQL after 12 months of transcripts | 0.05 TB | 0.48 TB | 4.8 TB |
| Search index after 12 months | 48 GB | 479 GB | 4.8 TB |
| Search partitions / replicas | 1 / 3 | 5 / 3 | 43 / 4 |
| Audio in storage at 30 days average retention | 0.3 TB | 3 TB | 30 TB |

## 4. What the numbers decide

1. **The API is not the bottleneck.** Three small replicas carry 100 000 users of interactive load on this model. The reason for three is availability (one per zone), not throughput. Scaling the API is therefore a rule on concurrent requests, not a capacity plan.
2. **The model quotas are the first wall.** At 10 000 users the peak needs about 230 k chat and 120 k extraction tokens per minute. Tokens-per-minute quota is granted per deployment and region, and a rate limit is also the user-visible failure. The AI gateway (ADR-016) spreads load over two regions and enforces a budget per caller so one tenant cannot consume the other's; the quota request is the long-lead item before launch.
3. **The index is the largest stock.** 479 GB at 10 000 users is five partitions. Float32 vectors at 1 536 dimensions would be twelve times that (57 TB at 100 000 users). The 512-dimension int8 choice is what keeps the index affordable, and it must be validated on retrieval quality with the golden set before it is fixed (a quality loss here would make the saving worthless).
4. **Waiting is the expensive way to process audio, and it is no longer what the worker does.** A worker that sat on a speech job for its whole duration needed several times the executions of one that computes and goes away. Speech now runs in stages (ADR-020): submit, leave, and a scheduled poll with backoff (60, 90, 135, 202, then 300 seconds) collects the result. Each poll is a short job execution, so the staged figure counts them (about seven polls and twenty seconds each per audio hour, both assumed): see the table. The poll interval is the knob: shorter means faster transcripts and more executions.
5. **PostgreSQL stays on one server for a long time.** 0.5 TB at 10 000 users is inside one General Purpose server; connections are 30 because the API pools per replica. The point to revisit is about 2 TB or a sustained CPU above 60%, and the first moves are read replicas for the graph and list queries (the standby replica already exists for disaster recovery), then partitioning the segment table by month. PgBouncer is not used: row level security relies on per-transaction settings and the connection count is far below the limit.

## 5. Launch preset and growth triggers

`envs/prod` is sized for **about 1 000 users**: 3 API replicas of 1 vCPU, General Purpose D4ds_v5 with zone-redundant HA, Search Standard with one partition and three replicas, Service Bus Premium with one messaging unit, API Management Premium with one unit per region.

| Signal (alert exists) | Threshold | Action |
|---|---|---|
| Search storage | 60% of the partitions | add a partition (the index rebuilds from PostgreSQL, ADR-007) |
| Model 429s at the AI gateway | any sustained | request quota; shift traffic to the second region |
| Queue backlog | 200 messages for 15 minutes | raise `worker_max_executions`, then look at Speech concurrency |
| PostgreSQL CPU / storage | 80% for 15 minutes / 85% | move one SKU up / raise storage |
| Gateway capacity | 70% | add a gateway unit |
| API p95 | above objective | the model says this is never CPU; look at PostgreSQL and the model first |

## 6. What to measure first (and replace here)

In this order, because each unlocks the next: tokens per audio hour on real meetings in all four languages; phrases per hour; the real speech turnaround; request mix per active user from the first week of beta; the API throughput on the production SKU (`bench_api.py` against the deployed revision). Each replaces a row in section 2 and the table is recomputed.
