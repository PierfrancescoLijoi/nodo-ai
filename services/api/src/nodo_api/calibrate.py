"""How well does this embedding model find graph entities by meaning? (ADR-024)

    python -m nodo_api.admin calibrate-entities

Embedding models differ a great deal in how they score related and unrelated text: the local E5
model puts everything between about 0.68 and 0.82, an OpenAI embedding model spreads it from near
zero to near one. The similarity above which an entity counts as "meant by the question" therefore
belongs to the model. This measures it on a fixed set: short entity labels in English, questions
about them in English, Italian, Polish and Danish, and questions about nothing the labels cover.
It reports how often the right entity is the nearest, how the right scores and the unrelated scores
are distributed, and the threshold that separates them best. Run it after choosing or changing the
embedding model, and set `NODO_ENTITY_MIN_SIMILARITY` to the suggestion if it differs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LABELS = (
    "Ingestion",
    "Keep the flow synchronous",
    "Budget review",
    "Retry policy for the queue",
    "Mobile app release",
    "Security audit deadline",
    "Hiring plan for the data team",
    "Customer onboarding",
)

# (question, the label it is about)
QUESTIONS = (
    ("What did we decide about ingestion?", "Ingestion"),
    ("Cosa abbiamo deciso sull'inserimento dei dati?", "Ingestion"),
    ("Co postanowiono w sprawie pobierania danych?", "Ingestion"),
    ("Hvad besluttede vi om indlæsning af data?", "Ingestion"),
    ("Who proposed keeping the flow synchronous?", "Keep the flow synchronous"),
    ("Chi ha proposto di mantenere il flusso sincrono?", "Keep the flow synchronous"),
    ("Kto zaproponował pozostawienie przepływu synchronicznym?", "Keep the flow synchronous"),
    ("Hvem foreslog at beholde flowet synkront?", "Keep the flow synchronous"),
    ("What happened in the budget review?", "Budget review"),
    ("Cosa è emerso dalla revisione del budget?", "Budget review"),
    ("Co ustalono podczas przeglądu budżetu?", "Budget review"),
    ("Hvad blev besluttet om budgettet?", "Budget review"),
    ("Who will test the retries on the queue?", "Retry policy for the queue"),
    ("Chi testerà i tentativi ripetuti sulla coda?", "Retry policy for the queue"),
    ("Kto przetestuje ponawianie prób w kolejce?", "Retry policy for the queue"),
    ("Hvem tester genforsøgene i køen?", "Retry policy for the queue"),
    ("When does the new phone app come out?", "Mobile app release"),
    ("Quando esce la nuova app per il telefono?", "Mobile app release"),
    ("Kiedy wyjdzie nowa aplikacja na telefon?", "Mobile app release"),
    ("Hvornår udkommer den nye mobilapp?", "Mobile app release"),
    ("When is the security audit due?", "Security audit deadline"),
    ("Entro quando va fatto l'audit di sicurezza?", "Security audit deadline"),
    ("Kiedy mija termin audytu bezpieczeństwa?", "Security audit deadline"),
    ("Hvornår skal sikkerhedsrevisionen være færdig?", "Security audit deadline"),
)

UNRELATED = (
    "What is the weather like on Mars today?",
    "Come si cucina la carbonara?",
    "Jak daleko jest stąd do Księżyca?",
    "Hvem vandt fodboldkampen i går?",
)


@dataclass
class Calibration:
    top1_correct: int = 0
    questions: int = 0
    right: list[float] = field(default_factory=list)  # score of the entity each question is about
    unrelated: list[float] = field(default_factory=list)  # best score of questions about nothing
    suggested: float | None = None
    separable: bool = False

    @property
    def accuracy(self) -> float:
        return self.top1_correct / self.questions if self.questions else 0.0


def _dot(a: list[float], b: list[float]) -> float:
    norm = (sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5) or 1.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


def calibrate(embedder: Any) -> Calibration:
    passages = getattr(embedder, "embed_passages", embedder.embed)
    ask = getattr(embedder, "embed_query", None)

    def question_vector(text: str) -> list[float]:
        return list(ask(text)[0] if ask else embedder.embed([text])[0])

    labels = [list(v) for v in passages(list(LABELS))]
    result = Calibration(questions=len(QUESTIONS))
    for question, truth in QUESTIONS:
        vector = question_vector(question)
        scores = [_dot(vector, label) for label in labels]
        best = max(range(len(LABELS)), key=scores.__getitem__)
        result.top1_correct += int(LABELS[best] == truth)
        result.right.append(scores[LABELS.index(truth)])
    for text in UNRELATED:
        vector = question_vector(text)
        result.unrelated.append(max(_dot(vector, label) for label in labels))
    low_right = sorted(result.right)[max(0, len(result.right) // 10)]  # tolerate the weakest 10%
    high_wrong = max(result.unrelated)
    result.separable = low_right > high_wrong
    result.suggested = (
        round((low_right + high_wrong) / 2, 3) if result.separable else round(high_wrong, 3)
    )
    return result


def render(report: Calibration) -> str:
    def stats(values: list[float]) -> str:
        ordered = sorted(values)
        return (
            f"min {ordered[0]:.3f}  median {ordered[len(ordered) // 2]:.3f}  max {ordered[-1]:.3f}"
        )

    lines = [
        f"nearest entity is the right one: {report.top1_correct}/{report.questions} "
        f"({report.accuracy:.0%})",
        f"score of the right entity:        {stats(report.right)}",
        f"best score of unrelated questions: {stats(report.unrelated)}",
    ]
    if report.separable:
        lines.append(
            f"suggested NODO_ENTITY_MIN_SIMILARITY = {report.suggested} "
            "(the right entities score above the unrelated ones)"
        )
    else:
        lines.append(
            f"the model does not separate them cleanly: {report.suggested} keeps every unrelated "
            "question out and loses some right entities; accept that, or use a stronger model"
        )
    return "\n".join(lines)
