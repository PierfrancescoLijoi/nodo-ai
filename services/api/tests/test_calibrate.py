"""The calibration that sets the entity-matching threshold for an embedding model (ADR-024)."""

from __future__ import annotations

from nodo_api.calibrate import LABELS, QUESTIONS, UNRELATED, calibrate, render


class Concepts:
    """A model that understands meaning perfectly: each question and its label share a direction."""

    def __init__(self, noise: float = 0.0, unrelated_score: float = 0.1) -> None:
        self.index = {label: i for i, label in enumerate(LABELS)}
        self.of = {question: self.index[label] for question, label in QUESTIONS}
        self.noise, self.unrelated_score = noise, unrelated_score

    def _vector(self, text: str) -> list[float]:
        size = len(LABELS) + 1
        vector = [0.0] * size
        if text in self.index:
            vector[self.index[text]] = 1.0
        elif text in self.of:
            vector[self.of[text]] = 1.0
            vector[-1] = self.noise
        else:  # unrelated: its closeness to the first label is `unrelated_score`, the rest its own
            vector[0] = self.unrelated_score
            vector[-1] = max(0.0, 1.0 - self.unrelated_score**2) ** 0.5
        return vector

    def embed(self, texts):
        return [self._vector(t) for t in texts]


def test_a_model_that_understands_meaning_gets_every_question_right_and_is_separable() -> None:
    report = calibrate(Concepts())
    assert report.top1_correct == len(QUESTIONS) and report.accuracy == 1.0
    assert report.separable and 0.1 < report.suggested < 1.0
    assert report.right and min(report.right) == 1.0
    assert len(report.unrelated) == len(UNRELATED)


def test_a_model_that_scores_unrelated_text_high_is_reported_as_not_separable() -> None:
    report = calibrate(Concepts(unrelated_score=1.0))
    assert not report.separable
    assert "does not separate" in render(report)


def test_the_report_names_the_setting_to_change() -> None:
    text = render(calibrate(Concepts()))
    assert "NODO_ENTITY_MIN_SIMILARITY" in text and "100%" in text


def test_the_query_side_of_the_model_is_used_for_questions_when_there_is_one() -> None:
    inner = Concepts()
    asked: list[str] = []

    class Split:
        def embed(self, texts):
            return inner.embed(texts)

        def embed_query(self, text):
            asked.append(text)
            return inner.embed([text])

    calibrate(Split())
    assert len(asked) == len(QUESTIONS) + len(UNRELATED)
