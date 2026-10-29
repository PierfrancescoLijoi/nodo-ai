"""Score live retrieval/query predictions against the NODO golden set.

Prediction JSONL schema::

    {"id":"case-id", "answerability":"supported", "citation_segment_ids":["seg"],
     "latency_ms":1234}

The scorer is intentionally model-independent: local and Azure runs produce the same format and
can therefore be compared without changing the evaluator.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)]


def score_predictions(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    case_ids = [case["id"] for case in cases]
    prediction_ids = [prediction["id"] for prediction in predictions]
    if len(prediction_ids) != len(set(prediction_ids)):
        raise ValueError("prediction ids must be unique")
    by_id = {prediction["id"]: prediction for prediction in predictions}
    missing = sorted(set(case_ids) - set(by_id))
    extra = sorted(set(by_id) - set(case_ids))

    true_citations = returned_citations = expected_citations = 0
    correct_answerability = leaks = 0
    latencies: list[float] = []
    category_scores: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        prediction = by_id.get(case["id"])
        if prediction is None:
            category_scores[case["category"]].append(False)
            continue
        returned = set(prediction.get("citation_segment_ids", []))
        expected = set(case["expected_citation_segment_ids"])
        forbidden = set(case["forbidden_segment_ids"])
        true_citations += len(returned & expected)
        returned_citations += len(returned)
        expected_citations += len(expected)
        correct = prediction.get("answerability") == case["expected_answerability"]
        correct_answerability += int(correct)
        category_scores[case["category"]].append(correct)
        leaks += len(returned & forbidden)
        if "latency_ms" in prediction:
            latency = float(prediction["latency_ms"])
            if latency < 0:
                raise ValueError(f"{case['id']}: latency_ms cannot be negative")
            latencies.append(latency)

    coverage = (len(cases) - len(missing)) / max(len(cases), 1)
    citation_precision = true_citations / max(returned_citations, 1)
    citation_recall = true_citations / max(expected_citations, 1)
    answerability_accuracy = correct_answerability / max(len(cases), 1)
    failed = []
    if coverage < 1:
        failed.append("coverage")
    if citation_precision < 0.95:
        failed.append("citation_precision")
    if citation_recall < 0.90:
        failed.append("citation_recall")
    if answerability_accuracy < 0.95:
        failed.append("answerability_accuracy")
    if leaks:
        failed.append("forbidden_leaks")
    latency_p95 = _p95(latencies)
    if latency_p95 is not None and latency_p95 > 6_000:
        failed.append("latency_p95")
    return {
        "cases": len(cases),
        "coverage": coverage,
        "missing_case_ids": missing,
        "extra_prediction_ids": extra,
        "citation_precision": citation_precision,
        "citation_recall": citation_recall,
        "answerability_accuracy": answerability_accuracy,
        "forbidden_leak_count": leaks,
        "latency_ms": {
            "samples": len(latencies),
            "p50": statistics.median(latencies) if latencies else None,
            "p95": latency_p95,
            "max": max(latencies) if latencies else None,
        },
        "by_category": {
            category: {
                "cases": len(values),
                "answerability_accuracy": sum(values) / len(values),
            }
            for category, values in sorted(category_scores.items())
        },
        "gate": {
            "status": "fail" if failed or extra else "pass",
            "failed_metrics": failed + (["unexpected_predictions"] if extra else []),
            "thresholds": {
                "coverage": 1.0,
                "citation_precision": 0.95,
                "citation_recall": 0.90,
                "answerability_accuracy": 0.95,
                "forbidden_leak_count": 0,
                "latency_p95_ms": 6_000,
            },
        },
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: score_results.py CASES.jsonl PREDICTIONS.jsonl [REPORT.json]",
            file=sys.stderr,
        )
        return 2
    report = score_predictions(_jsonl(Path(sys.argv[1])), _jsonl(Path(sys.argv[2])))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if len(sys.argv) == 4:
        Path(sys.argv[3]).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["gate"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
