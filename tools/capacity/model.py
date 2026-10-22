"""Capacity model of NODO AI on Azure, driven by what the application does (docs/CAPACITY-MODEL.md).

    python tools/capacity/model.py            # tables for 1k, 10k and 100k monthly users, as Markdown
    python tools/capacity/model.py 25000      # one more size

Every number that feeds the model is in `Assumptions`, with where it comes from: **measured** (a
script in this repository), **derived** (arithmetic on measured values) or **assumed** (a planning
value that must be replaced by a measurement; the Azure ones need a subscription). The output says
which resources scale with what, so a SKU is a consequence of the workload and not a guess.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Assumptions:
    # --- the people (assumed: a product decision to revisit with real usage)
    audio_hours_per_user_month: float = 10.0
    daily_active_ratio: float = 0.30
    api_requests_per_dau_day: float = 150.0  # list, detail, graph, status polling while processing
    questions_per_dau_day: float = 3.0
    peak_hour_share_of_day: float = 0.15  # traffic is concentrated in working hours
    burst_factor: float = 3.0  # peak second over the peak hour's average
    ingestion_peak_hour_share: float = 0.10  # meetings end together, at the top of the hour

    # --- one hour of audio
    audio_mb_per_hour: float = 30.0  # derived: 64 kbit/s AAC; the desktop app's Opus is 9 (measured)
    segments_per_hour: int = 600  # measured: 392 phrases for a 60 min test meeting, rounded up
    pg_mb_per_hour: float = 0.4  # derived: ~55 kB of text, versions, graph rows, indexes
    extract_tokens_in_per_hour: int = 16_000  # derived from the local windows (3 500 tokens + prompt)
    extract_tokens_out_per_hour: int = 6_000
    embed_tokens_per_hour: int = 25_000
    embedding_dimensions: int = 512  # text-embedding-3 can be asked for fewer dimensions
    vector_bytes_per_dimension: float = 1.0  # int8 scalar quantization (float32 would be 4)
    index_overhead: float = 1.3  # HNSW graph and text index on top of the vectors
    speech_wait_hours_per_audio_hour: float = 0.25  # ASSUMED, not measured: needs the subscription
    worker_cpu_seconds_per_hour: float = 60.0  # derived: indexing batches, graph writes, orchestration

    # --- one question
    query_tokens_in: int = 3_000
    query_tokens_out: int = 400

    # --- one replica (measured by services/api/scripts/bench_api.py, then halved for headroom)
    api_rps_per_vcpu: float = 150.0
    api_vcpu_per_replica: float = 2.0
    pg_pool_per_replica: int = 10
    min_replicas: int = 3  # one per availability zone

    # --- vendor limits (ASSUMED planning values: confirm against the quota pages and the portal)
    search_partition_gb: float = 160.0
    search_partition_fill: float = 0.7
    search_qps_per_replica: float = 10.0
    pg_comfortable_tb: float = 2.0  # one server, before backups and vacuum dominate
    audio_retention_days_avg: float = 30.0
    transcript_months_kept: float = 12.0


@dataclass(frozen=True)
class Result:
    users: int
    dau: int
    api_peak_rps: float
    api_replicas: int
    questions_peak_per_min: float
    chat_tpm_peak: int
    extraction_tpm_peak: int
    embedding_tpm_peak: int
    audio_hours_peak_per_hour: float
    executions_long_polling: int
    executions_staged: int
    pg_connections: int
    pg_tb_12m: float
    search_gb: float
    search_partitions: int
    search_replicas: int
    audio_stock_tb: float
    exports_note: str = ""


def model(users: int, a: Assumptions = Assumptions()) -> Result:
    dau = round(users * a.daily_active_ratio)
    api_peak_rps = dau * a.api_requests_per_dau_day * a.peak_hour_share_of_day / 3600 * a.burst_factor
    per_replica = a.api_rps_per_vcpu * a.api_vcpu_per_replica
    api_replicas = max(a.min_replicas, math.ceil(api_peak_rps / per_replica))

    questions_per_min = dau * a.questions_per_dau_day * a.peak_hour_share_of_day / 60 * a.burst_factor
    chat_tpm = round(questions_per_min * (a.query_tokens_in + a.query_tokens_out))

    audio_per_day = users * a.audio_hours_per_user_month / 30
    peak_audio_per_hour = audio_per_day * a.ingestion_peak_hour_share
    extraction_tpm = round(
        peak_audio_per_hour * (a.extract_tokens_in_per_hour + a.extract_tokens_out_per_hour) / 60
    )
    embedding_tpm = round(peak_audio_per_hour * a.embed_tokens_per_hour / 60)

    # A job that waits for the speech service holds a replica for the whole wait (long polling);
    # staged jobs hold one only while they compute, because the wait is a scheduled message.
    long_polling = math.ceil(peak_audio_per_hour * (a.speech_wait_hours_per_audio_hour + a.worker_cpu_seconds_per_hour / 3600))
    staged = math.ceil(peak_audio_per_hour * a.worker_cpu_seconds_per_hour / 3600)

    hours_12m = users * a.audio_hours_per_user_month * a.transcript_months_kept
    pg_tb = hours_12m * a.pg_mb_per_hour / 1_000_000
    vector_bytes = a.embedding_dimensions * a.vector_bytes_per_dimension
    search_gb = hours_12m * a.segments_per_hour * vector_bytes * a.index_overhead / 1e9
    partitions = max(1, math.ceil(search_gb / (a.search_partition_gb * a.search_partition_fill)))
    search_qps = questions_per_min / 60 * 2  # a question runs one hybrid search, plus retries
    search_replicas = max(3, math.ceil(search_qps / a.search_qps_per_replica) + 1)  # 3: read/write SLA

    audio_tb = users * a.audio_hours_per_user_month * a.audio_retention_days_avg / 30 * a.audio_mb_per_hour / 1e6
    return Result(
        users=users,
        dau=dau,
        api_peak_rps=api_peak_rps,
        api_replicas=api_replicas,
        questions_peak_per_min=questions_per_min,
        chat_tpm_peak=chat_tpm,
        extraction_tpm_peak=extraction_tpm,
        embedding_tpm_peak=embedding_tpm,
        audio_hours_peak_per_hour=peak_audio_per_hour,
        executions_long_polling=long_polling,
        executions_staged=staged,
        pg_connections=api_replicas * a.pg_pool_per_replica,
        pg_tb_12m=pg_tb,
        search_gb=search_gb,
        search_partitions=partitions,
        search_replicas=search_replicas,
        audio_stock_tb=audio_tb,
    )


def markdown(sizes: list[int], a: Assumptions = Assumptions()) -> str:
    results = [model(n, a) for n in sizes]
    head = "| | " + " | ".join(f"{r.users:,} users" for r in results) + " |\n"
    head += "|---|" + "---|" * len(results) + "\n"

    def row(label: str, getter) -> str:
        return f"| {label} | " + " | ".join(getter(r) for r in results) + " |\n"

    body = [
        row("Daily active users", lambda r: f"{r.dau:,}"),
        row("API peak requests per second", lambda r: f"{r.api_peak_rps:,.1f}"),
        row("API replicas (2 vCPU, at least one per zone)", lambda r: str(r.api_replicas)),
        row("PostgreSQL connections from the API", lambda r: str(r.pg_connections)),
        row("Questions per minute at peak", lambda r: f"{r.questions_peak_per_min:,.0f}"),
        row("Chat model tokens per minute at peak", lambda r: f"{r.chat_tpm_peak:,}"),
        row("Audio hours arriving in the peak hour", lambda r: f"{r.audio_hours_peak_per_hour:,.0f}"),
        row("Extraction model tokens per minute at peak", lambda r: f"{r.extraction_tpm_peak:,}"),
        row("Embedding tokens per minute at peak", lambda r: f"{r.embedding_tpm_peak:,}"),
        row("Worker executions, job waits for speech", lambda r: f"{r.executions_long_polling:,}"),
        row("Worker executions, staged jobs", lambda r: f"{r.executions_staged:,}"),
        row("PostgreSQL after 12 months of transcripts", lambda r: f"{r.pg_tb_12m:,.2f} TB"),
        row("Search index after 12 months", lambda r: f"{r.search_gb:,.0f} GB"),
        row("Search partitions / replicas", lambda r: f"{r.search_partitions} / {r.search_replicas}"),
        row("Audio in storage (average retention)", lambda r: f"{r.audio_stock_tb:,.1f} TB"),
    ]
    return head + "".join(body)


def what_if_float32_vectors(users: int) -> float:
    """Index size with full-precision 1536-dimension vectors, to show why the model asks for less."""
    heavy = replace(Assumptions(), embedding_dimensions=1536, vector_bytes_per_dimension=4.0)
    return model(users, heavy).search_gb


if __name__ == "__main__":
    wanted = [int(x) for x in sys.argv[1:]] or [1_000, 10_000, 100_000]
    print(markdown(wanted))
    print()
    print(f"Index at {wanted[-1]:,} users with float32 1536-d vectors: "
          f"{what_if_float32_vectors(wanted[-1]):,.0f} GB (against {model(wanted[-1]).search_gb:,.0f} GB)")
