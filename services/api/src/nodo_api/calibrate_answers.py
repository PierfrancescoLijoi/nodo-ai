"""How safely does this embedding model tell a repeated question from a different one? (ADR-026)

    python -m nodo_api.admin calibrate-answer-cache

An answer cache is only as good as its notion of "the same question". This measures it on pairs in
English, Italian, Polish and Danish: the same question in other words, and questions that read alike
but ask something else (another person, another month, the opposite, a denial). Pairs the cache
already tells apart by what it compares besides similarity (numbers, names, negation) are reported
separately: they can not produce a false hit. The suggested NODO_ANSWER_CACHE_MIN_SIMILARITY keeps
every remaining look-alike out; the share of repeats it still catches says what the cache is worth
with this model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nodo_api.calibrate import _dot
from nodo_api.services.answer_cache import discriminators

MARGIN = 0.005

SAME_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "What did we decide about the ingestion pipeline?",
        "What was decided regarding the ingestion pipeline?",
    ),
    ("Who is responsible for the security audit?", "Who owns the security audit?"),
    ("When is the mobile app released?", "What is the release date of the mobile app?"),
    ("What happened in the budget review?", "What came out of the budget review?"),
    (
        "Cosa abbiamo deciso sull'inserimento dei dati?",
        "Cosa è stato deciso riguardo all'inserimento dei dati?",
    ),
    ("Chi è responsabile dell'audit di sicurezza?", "Di chi è l'audit di sicurezza?"),
    ("Quando esce la nuova app?", "Qual è la data di uscita della nuova app?"),
    (
        "Cosa è emerso dalla revisione del budget?",
        "Che cosa è stato detto nella revisione del budget?",
    ),
    (
        "Co postanowiono w sprawie pobierania danych?",
        "Jakie były ustalenia dotyczące pobierania danych?",
    ),
    ("Kto odpowiada za audyt bezpieczeństwa?", "Do kogo należy audyt bezpieczeństwa?"),
    ("Kiedy wyjdzie nowa aplikacja?", "Jaka jest data premiery nowej aplikacji?"),
    ("Co ustalono podczas przeglądu budżetu?", "Jakie wnioski padły na przeglądzie budżetu?"),
    ("Hvad besluttede vi om indlæsning af data?", "Hvad blev der besluttet om indlæsning af data?"),
    ("Hvem er ansvarlig for sikkerhedsrevisionen?", "Hvem ejer sikkerhedsrevisionen?"),
    ("Hvornår udkommer den nye app?", "Hvad er udgivelsesdatoen for den nye app?"),
    ("Hvad skete der i budgetgennemgangen?", "Hvad kom der ud af budgetgennemgangen?"),
)

DIFFERENT_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "What did we decide about the ingestion pipeline?",
        "What did we not decide about the ingestion pipeline?",
    ),
    ("Who proposed the async queue?", "Who opposed the async queue?"),
    ("What happened in the budget review?", "What happened in the security review?"),
    ("Who is responsible for the security audit?", "Who is responsible for the budget review?"),
    ("What did Marco say about the release?", "What did Sara say about the release?"),
    ("What was decided in March about hiring?", "What was decided in April about hiring?"),
    ("List the actions assigned to Marco", "List the actions assigned to Sara"),
    ("Cosa ha detto Marco sul rilascio?", "Cosa ha detto Sara sul rilascio?"),
    (
        "Cosa abbiamo deciso sull'inserimento dei dati?",
        "Cosa non abbiamo deciso sull'inserimento dei dati?",
    ),
    ("Chi ha proposto la coda asincrona?", "Chi si è opposto alla coda asincrona?"),
    ("Cosa si è deciso a marzo sulle assunzioni?", "Cosa si è deciso ad aprile sulle assunzioni?"),
    ("Co powiedział Marco o wydaniu?", "Co powiedziała Sara o wydaniu?"),
    ("Kto zaproponował kolejkę asynchroniczną?", "Kto sprzeciwił się kolejce asynchronicznej?"),
    ("Co ustalono w marcu w sprawie rekrutacji?", "Co ustalono w kwietniu w sprawie rekrutacji?"),
    ("Hvad sagde Marco om udgivelsen?", "Hvad sagde Sara om udgivelsen?"),
    ("Hvem foreslog den asynkrone kø?", "Hvem modsatte sig den asynkrone kø?"),
    ("Hvad blev besluttet i marts om ansættelser?", "Hvad blev besluttet i april om ansættelser?"),
)


@dataclass
class AnswerCacheCalibration:
    repeats: list[float] = field(default_factory=list)  # the same question in other words
    lookalikes: list[float] = field(default_factory=list)  # different, and not told apart by words
    guarded: int = 0  # different, but numbers, names or negation already tell them apart
    suggested: float = 1.0

    @property
    def caught(self) -> float:
        hits = sum(1 for score in self.repeats if score >= self.suggested)
        return hits / len(self.repeats) if self.repeats else 0.0


def calibrate_answer_cache(embed_question: Any) -> AnswerCacheCalibration:
    def score(a: str, b: str) -> float:
        return _dot(list(embed_question(a)), list(embed_question(b)))

    report = AnswerCacheCalibration()
    report.repeats = [score(a, b) for a, b in SAME_QUESTIONS]
    for a, b in DIFFERENT_QUESTIONS:
        if discriminators(a) != discriminators(b):
            report.guarded += 1
        else:
            report.lookalikes.append(score(a, b))
    worst = max(report.lookalikes, default=0.0)
    report.suggested = round(min(1.0, worst + MARGIN), 3)
    return report


def render_answer_cache(report: AnswerCacheCalibration) -> str:
    def stats(values: list[float]) -> str:
        ordered = sorted(values)
        if not ordered:
            return "none"
        return (
            f"min {ordered[0]:.3f}  median {ordered[len(ordered) // 2]:.3f}  max {ordered[-1]:.3f}"
        )

    return "\n".join(
        [
            f"the same question in other words: {stats(report.repeats)}",
            f"look-alikes the words do not tell apart: {stats(report.lookalikes)}",
            f"different questions already told apart by words (numbers, names, negation): "
            f"{report.guarded}",
            f"suggested NODO_ANSWER_CACHE_MIN_SIMILARITY = {report.suggested} "
            f"(catches {report.caught:.0%} of the repeats)",
            "a small set: when in doubt raise the value; a wrong answer costs more than a slow one",
        ]
    )
