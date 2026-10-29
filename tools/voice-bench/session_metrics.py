"""Aggregate real-time voice session traces into release gates.

Input JSONL contains one ``SessionObservation``-shaped object per completed session. The load
generator is free to use WebSocket or WebRTC; the result contract stays transport-independent.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionObservation:
    session_id: str
    expected_frames: int
    received_frames: int
    recovered_frames: int
    duplicate_frames: int
    partial_latencies_ms: tuple[float, ...] = ()
    final_latencies_ms: tuple[float, ...] = ()
    first_audio_latencies_ms: tuple[float, ...] = ()
    barge_in_stop_latencies_ms: tuple[float, ...] = ()
    reconnect_latencies_ms: tuple[float, ...] = ()
    success: bool = False


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(math.ceil(quantile * len(ordered)) - 1, 0)])


def _latency(values: list[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50": round(statistics.median(values), 3) if values else None,
        "p95": round(_percentile(values, 0.95), 3) if values else None,
        "p99": round(_percentile(values, 0.99), 3) if values else None,
        "max": round(max(values), 3) if values else None,
    }


def summarize_sessions(
    observations: Iterable[SessionObservation],
    *,
    min_sessions: int = 100,
    require_interactive: bool = True,
) -> dict[str, Any]:
    rows = list(observations)
    if not rows:
        raise ValueError("at least one session is required")
    for row in rows:
        if row.expected_frames <= 0:
            raise ValueError(f"{row.session_id}: expected_frames must be positive")
        if min(row.received_frames, row.recovered_frames, row.duplicate_frames) < 0:
            raise ValueError(f"{row.session_id}: frame counts cannot be negative")
        if row.received_frames + row.recovered_frames > row.expected_frames:
            raise ValueError(
                f"{row.session_id}: unique delivered frames exceed expected_frames"
            )

    expected = sum(row.expected_frames for row in rows)
    delivered = sum(
        min(row.expected_frames, row.received_frames + row.recovered_frames)
        for row in rows
    )
    duplicates = sum(row.duplicate_frames for row in rows)
    partial = [value for row in rows for value in row.partial_latencies_ms]
    final = [value for row in rows for value in row.final_latencies_ms]
    first_audio = [value for row in rows for value in row.first_audio_latencies_ms]
    barge_in = [value for row in rows for value in row.barge_in_stop_latencies_ms]
    reconnect = [value for row in rows for value in row.reconnect_latencies_ms]
    continuity = delivered / expected
    success_rate = sum(row.success for row in rows) / len(rows)
    metrics = {
        "partial_latency_p95": (_percentile(partial, 0.95), 1_000),
        "final_latency_p95": (_percentile(final, 0.95), 2_000),
        "reconnect_p95": (_percentile(reconnect, 0.95), 5_000),
    }
    if require_interactive:
        metrics |= {
            "first_audio_p95": (_percentile(first_audio, 0.95), 2_000),
            "barge_in_stop_p95": (_percentile(barge_in, 0.95), 300),
        }
    failed = [
        name
        for name, (value, limit) in metrics.items()
        if value is None or value > limit
    ]
    if continuity < 0.999:
        failed.append("audio_continuity")
    if success_rate < 0.995:
        failed.append("session_success_rate")
    shortfall = max(min_sessions - len(rows), 0)
    status = "insufficient_data" if shortfall else ("fail" if failed else "pass")
    return {
        "sessions": len(rows),
        "audio_continuity": round(continuity, 6),
        "duplicate_frame_rate": round(duplicates / expected, 6),
        "session_success_rate": round(success_rate, 6),
        "partial_latency_ms": _latency(partial),
        "final_latency_ms": _latency(final),
        "first_audio_latency_ms": _latency(first_audio),
        "barge_in_stop_ms": _latency(barge_in),
        "reconnect_ms": _latency(reconnect),
        "gate": {
            "status": status,
            "failed_metrics": failed,
            "session_shortfall": shortfall,
            "thresholds": {
                "partial_p95_ms": 1_000,
                "final_p95_ms": 2_000,
                "first_audio_p95_ms": 2_000 if require_interactive else None,
                "barge_in_stop_p95_ms": 300 if require_interactive else None,
                "reconnect_p95_ms": 5_000,
                "audio_continuity": 0.999,
                "session_success_rate": 0.995,
                "min_sessions": min_sessions,
            },
        },
    }


def _observation(row: dict[str, Any]) -> SessionObservation:
    def values(name: str) -> tuple[float, ...]:
        return tuple(float(value) for value in row.get(name, ()))

    return SessionObservation(
        session_id=str(row["session_id"]),
        expected_frames=int(row["expected_frames"]),
        received_frames=int(row["received_frames"]),
        recovered_frames=int(row.get("recovered_frames", 0)),
        duplicate_frames=int(row.get("duplicate_frames", 0)),
        partial_latencies_ms=values("partial_latencies_ms"),
        final_latencies_ms=values("final_latencies_ms"),
        first_audio_latencies_ms=values("first_audio_latencies_ms"),
        barge_in_stop_latencies_ms=values("barge_in_stop_latencies_ms"),
        reconnect_latencies_ms=values("reconnect_latencies_ms"),
        success=bool(row["success"]),
    )


def main() -> int:
    if len(sys.argv) not in (3, 4, 5):
        print(
            "usage: session_metrics.py SESSIONS.jsonl REPORT.json [MIN_SESSIONS] [meeting|assistant]",
            file=sys.stderr,
        )
        return 2
    rows = [
        _observation(json.loads(line))
        for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mode = sys.argv[4] if len(sys.argv) == 5 else "assistant"
    if mode not in {"meeting", "assistant"}:
        raise ValueError("mode must be meeting or assistant")
    report = summarize_sessions(
        rows,
        min_sessions=int(sys.argv[3]) if len(sys.argv) >= 4 else 100,
        require_interactive=mode == "assistant",
    )
    Path(sys.argv[2]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["gate"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
