"""Stable quality metrics for speech benchmarks.

The module deliberately has no model or cloud dependency. Benchmark runners emit observations;
this module turns them into a comparable report with deterministic confidence intervals.
"""

from __future__ import annotations

import math
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from score import speaker_accuracy

Interval = tuple[str, float, float]
MONTHS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
}


@dataclass(frozen=True)
class TranscriptObservation:
    case_id: str
    locale: str
    scenario: str
    reference: str
    hypothesis: str
    audio_seconds: float
    wall_seconds: float
    reference_speakers: tuple[Interval, ...] = ()
    hypothesis_speakers: tuple[Interval, ...] = ()


def observation_from_result(row: dict[str, Any]) -> TranscriptObservation:
    required = (
        "file",
        "locale",
        "scenario",
        "reference",
        "hypothesis",
        "audio_s",
        "wall_s",
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise ValueError(f"result is missing fields: {', '.join(missing)}")
    audio_seconds = float(row["audio_s"])
    wall_seconds = float(row["wall_s"])
    if audio_seconds <= 0:
        raise ValueError("audio_s must be positive")
    if wall_seconds < 0:
        raise ValueError("wall_s cannot be negative")

    def intervals(field: str) -> tuple[Interval, ...]:
        return tuple(
            (str(item[0]), float(item[1]), float(item[2]))
            for item in row.get(field, ())
        )

    return TranscriptObservation(
        case_id=str(row["file"]),
        locale=str(row["locale"]),
        scenario=str(row["scenario"]),
        reference=str(row["reference"]),
        hypothesis=str(row["hypothesis"]),
        audio_seconds=audio_seconds,
        wall_seconds=wall_seconds,
        reference_speakers=intervals("reference_speakers"),
        hypothesis_speakers=intervals("hypothesis_speakers"),
    )


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("'", " ")
    return re.findall(r"[^\W_]+", normalized)


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for index, expected in enumerate(reference, start=1):
        row = [index]
        for offset, actual in enumerate(hypothesis, start=1):
            row.append(
                min(
                    previous[offset] + 1,
                    row[offset - 1] + 1,
                    previous[offset - 1] + (expected != actual),
                )
            )
        previous = row
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    expected, actual = _tokens(reference), _tokens(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    return _edit_distance(expected, actual) / len(expected)


def character_error_rate(reference: str, hypothesis: str) -> float:
    expected = list(unicodedata.normalize("NFKC", reference).casefold())
    actual = list(unicodedata.normalize("NFKC", hypothesis).casefold())
    if not expected:
        return 0.0 if not actual else 1.0
    return _edit_distance(expected, actual) / len(expected)


def _entities(text: str) -> set[str]:
    candidates = re.findall(
        r"\b(?:[A-ZÀ-ÖØ-Þ][\wÀ-ÿ+.-]*)(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÿ+.-]*)*", text
    )
    return {item.casefold() for item in candidates if item.casefold() not in MONTHS}


def entity_recall(reference: str, hypothesis: str) -> float:
    expected = _entities(reference)
    if not expected:
        return 1.0
    return len(expected & _entities(hypothesis)) / len(expected)


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)?)(?!\w)", text))


def number_recall(reference: str, hypothesis: str) -> float:
    expected = _numbers(reference)
    if not expected:
        return 1.0
    return len(expected & _numbers(hypothesis)) / len(expected)


def nearest_rank_percentile(samples: Sequence[float], quantile: float) -> float:
    if not samples:
        raise ValueError("percentile needs at least one sample")
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(samples)
    return ordered[max(math.ceil(quantile * len(ordered)) - 1, 0)]


def bootstrap_interval(
    samples: Sequence[float], *, seed: int = 1729, resamples: int = 2_000
) -> tuple[float, float]:
    if not samples:
        raise ValueError("confidence interval needs at least one sample")
    if resamples < 20:
        raise ValueError("at least 20 bootstrap resamples are required")
    if len(samples) == 1:
        return samples[0], samples[0]
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(samples) for _ in samples) for _ in range(resamples)
    )
    return (
        nearest_rank_percentile(means, 0.025),
        nearest_rank_percentile(means, 0.975),
    )


def _round(value: float) -> float:
    return round(value, 4)


def _quality_slice(rows: Sequence[TranscriptObservation]) -> dict[str, Any]:
    wers = [word_error_rate(row.reference, row.hypothesis) for row in rows]
    total_words = sum(len(_tokens(row.reference)) for row in rows)
    total_errors = sum(
        _edit_distance(_tokens(row.reference), _tokens(row.hypothesis)) for row in rows
    )
    return {
        "samples": len(rows),
        "wer_micro": _round(total_errors / max(total_words, 1)),
        "wer_macro": _round(statistics.fmean(wers)),
        "cer_macro": _round(
            statistics.fmean(
                character_error_rate(row.reference, row.hypothesis) for row in rows
            )
        ),
        "entity_recall": _round(
            statistics.fmean(
                entity_recall(row.reference, row.hypothesis) for row in rows
            )
        ),
        "number_recall": _round(
            statistics.fmean(
                number_recall(row.reference, row.hypothesis) for row in rows
            )
        ),
    }


def _grouped(rows: Sequence[TranscriptObservation], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[TranscriptObservation]] = defaultdict(list)
    for row in rows:
        groups[getattr(row, field)].append(row)
    return [
        {field: key, **_quality_slice(group)} for key, group in sorted(groups.items())
    ]


def summarize(
    observations: Iterable[TranscriptObservation],
    *,
    min_samples: int = 30,
    max_wer: float = 0.15,
    min_speaker_accuracy: float = 0.90,
    max_rtf: float = 25 / 60,
    bootstrap_resamples: int = 2_000,
) -> dict[str, Any]:
    rows = list(observations)
    if not rows:
        raise ValueError("at least one observation is required")
    latency = [row.wall_seconds for row in rows]
    rtf = [row.wall_seconds / row.audio_seconds for row in rows]
    wers = [word_error_rate(row.reference, row.hypothesis) for row in rows]
    speaker = [
        speaker_accuracy(row.reference_speakers, row.hypothesis_speakers)
        for row in rows
        if row.reference_speakers
    ]
    quality = _quality_slice(rows)
    shortfall = max(min_samples - len(rows), 0)
    failed = []
    if quality["wer_micro"] > max_wer:
        failed.append("wer")
    if speaker and statistics.fmean(speaker) < min_speaker_accuracy:
        failed.append("speaker_accuracy")
    if nearest_rank_percentile(rtf, 0.95) > max_rtf:
        failed.append("rtf")
    status = "insufficient_data" if shortfall else ("fail" if failed else "pass")
    low, high = bootstrap_interval(wers, resamples=bootstrap_resamples)
    return {
        "samples": len(rows),
        "audio_hours": round(sum(row.audio_seconds for row in rows) / 3600, 4),
        "wer": {
            "micro": quality["wer_micro"],
            "macro": quality["wer_macro"],
            "macro_ci95": [_round(low), _round(high)],
        },
        "cer": {"macro": quality["cer_macro"]},
        "entity_recall": quality["entity_recall"],
        "number_recall": quality["number_recall"],
        "latency_seconds": {
            "p50": _round(nearest_rank_percentile(latency, 0.50)),
            "p95": _round(nearest_rank_percentile(latency, 0.95)),
            "max": _round(max(latency)),
        },
        "rtf": {
            "p50": _round(nearest_rank_percentile(rtf, 0.50)),
            "p95": _round(nearest_rank_percentile(rtf, 0.95)),
        },
        "speaker_accuracy": {
            "mean": _round(statistics.fmean(speaker)) if speaker else None,
            "samples": len(speaker),
        },
        "by_locale": _grouped(rows, "locale"),
        "by_scenario": _grouped(rows, "scenario"),
        "gate": {
            "status": status,
            "failed_metrics": failed,
            "sample_shortfall": shortfall,
            "thresholds": {
                "max_wer": max_wer,
                "min_speaker_accuracy": min_speaker_accuracy,
                "max_rtf": _round(max_rtf),
                "min_samples": min_samples,
            },
        },
    }
