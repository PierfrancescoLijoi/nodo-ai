"""Scoring shared by the local Whisper benchmark and the Azure Speech benchmark.

`speaker_accuracy` answers "of the time people really spoke, how much was attributed to the right
speaker": the labels a recogniser invents (S1, S2, ...) are mapped to the true speakers in the best
possible way, then correctly attributed time is divided by true speech time. It ignores silence, so
it is not the DER of the literature (no false alarm or missed speech term); it is the number that
matters for "who said this" in the app.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import permutations

Interval = tuple[str, float, float]  # speaker, start seconds, end seconds


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def speaker_accuracy(reference: Sequence[Interval], hypothesis: Sequence[Interval]) -> float:
    truth = sorted({s for s, _, _ in reference})
    guessed = sorted({s for s, _, _ in hypothesis})
    if not truth:
        return 0.0
    # time shared by each (true speaker, guessed label)
    shared = {
        (t, g): sum(
            overlap((rs, re), (hs, he))
            for r, rs, re in reference
            if r == t
            for h, hs, he in hypothesis
            if h == g
        )
        for t in truth
        for g in guessed
    }
    total = sum(e - s for _, s, e in reference)
    best = 0.0
    width = max(len(truth), len(guessed))
    slots = [*guessed, *[None] * (width - len(guessed))]
    for order in permutations(slots, len(truth)):  # speakers are few: brute force is exact
        best = max(best, sum(shared.get((t, g), 0.0) for t, g in zip(truth, order, strict=True)))
    return best / total if total else 0.0


def speaker_count_error(reference: Sequence[Interval], hypothesis: Sequence[Interval]) -> int:
    return len({s for s, _, _ in hypothesis}) - len({s for s, _, _ in reference})
