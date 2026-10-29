"""Aggregate provider JSONL into a machine-readable speech quality gate.

Usage:
    python tools/speech-bench/quality_report.py RESULTS.jsonl REPORT.json [MIN_SAMPLES]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from quality import observation_from_result, summarize


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: quality_report.py RESULTS.jsonl REPORT.json [MIN_SAMPLES]",
            file=sys.stderr,
        )
        return 2
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    observations = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            observations.append(observation_from_result(json.loads(line)))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{source}:{line_number}: {error}") from error
    report = summarize(
        observations, min_samples=int(sys.argv[3]) if len(sys.argv) == 4 else 30
    )
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["gate"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
