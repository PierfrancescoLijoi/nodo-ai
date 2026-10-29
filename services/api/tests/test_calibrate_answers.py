"""The calibration that sets the answer-cache threshold for an embedding model (ADR-026)."""

from __future__ import annotations

from nodo_api.calibrate_answers import (
    DIFFERENT_QUESTIONS,
    SAME_QUESTIONS,
    calibrate_answer_cache,
    render_answer_cache,
)


class Pairs:
    """Repeats are 0.9 apart in angle terms from each other by construction; look-alikes closer."""

    def __init__(self, repeat: float, lookalike: float) -> None:
        self.repeat, self.lookalike = repeat, lookalike
        self.vectors: dict[str, list[float]] = {}
        for index, (a, b) in enumerate(SAME_QUESTIONS):
            self._pair(a, b, repeat, 2 * index)
        for index, (a, b) in enumerate(DIFFERENT_QUESTIONS):
            self._pair(a, b, lookalike, 2 * index + 1)

    def _pair(self, a: str, b: str, cosine: float, salt: int) -> None:
        # two unit vectors with the given cosine, each pair on its own axes
        size = 2 * (len(SAME_QUESTIONS) + len(DIFFERENT_QUESTIONS)) + 2
        first = [0.0] * size
        second = [0.0] * size
        first[salt] = 1.0
        second[salt] = cosine
        second[salt + 1] = (1 - cosine**2) ** 0.5
        self.vectors.setdefault(a, first)
        self.vectors.setdefault(b, second)

    def __call__(self, text: str) -> list[float]:
        return self.vectors[text]


def test_the_threshold_sits_just_above_the_worst_lookalike_the_words_do_not_catch() -> None:
    report = calibrate_answer_cache(Pairs(repeat=0.97, lookalike=0.9))
    assert report.suggested == 0.905 and report.caught == 1.0
    assert report.guarded + len(report.lookalikes) == len(DIFFERENT_QUESTIONS)
    assert report.guarded > 0  # names, months as numbers or denials are told apart without scores


def test_a_model_that_scores_lookalikes_above_repeats_catches_none() -> None:
    report = calibrate_answer_cache(Pairs(repeat=0.9, lookalike=0.97))
    assert report.suggested > 0.97 and report.caught == 0.0
    text = render_answer_cache(report)
    assert "catches 0%" in text and "NODO_ANSWER_CACHE_MIN_SIMILARITY" in text
