"""Measure cold and warm latency of the optional local ONNX reranker.

Run from services/api with the local dependencies installed:

    python scripts/bench_reranker.py [candidates=20] [iterations=100]
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[1] / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction) - 1))]


def main() -> int:
    from nodo_api.adapters.local.models import LocalModelPaths
    from nodo_api.adapters.local.reranker import DEFAULT_MODEL, OnnxReranker

    candidates = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    iterations = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    folder = LocalModelPaths.discover().reranker_dir
    if not OnnxReranker.installed(folder):
        print("reranker is not installed; run tools/local-setup/install_models.py --only reranker")
        return 2
    reranker = OnnxReranker(folder)
    documents = [
        f"Meeting passage {index}: the Aurora roadmap review is on Friday at ten."
        for index in range(candidates)
    ]
    started = time.perf_counter()
    reranker.score("When is the Aurora roadmap review?", documents)
    cold_ms = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        reranker.score("When is the Aurora roadmap review?", documents)
        samples.append((time.perf_counter() - started) * 1000)
    print(
        json.dumps(
            {
                "model": DEFAULT_MODEL,
                "candidates": candidates,
                "iterations": iterations,
                "cold_ms": round(cold_ms, 2),
                "warm_p50_ms": round(statistics.median(samples), 2),
                "warm_p95_ms": round(percentile(samples, 0.95), 2),
                "warm_p99_ms": round(percentile(samples, 0.99), 2),
                "warm_mean_ms": round(statistics.fmean(samples), 2),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
