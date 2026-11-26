"""Deterministic, versioned question variants for the NODO golden set."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

# Six hand-authored cases per category live in cases.jsonl. These nine wording variants per
# category make the evaluation set 150 cases without cloning evidence annotations by hand.
VARIANT_PREFIXES = {
    "en": "Please answer precisely: ",
    "it": "Rispondi con precisione: ",
    "pl": "Odpowiedz precyzyjnie: ",
    "da": "Svar præcist: ",
}
VARIANTS_PER_CATEGORY = 9


def expanded_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["category"]].append(case)
    variants: list[dict[str, Any]] = []
    for category in sorted(grouped):
        originals = grouped[category]
        for number in range(VARIANTS_PER_CATEGORY):
            variant = deepcopy(originals[number % len(originals)])
            variant["id"] = f"{variant['id']}-v{number + 1}"
            question = variant["question"]
            variant["question"] = (
                {locale: f"{VARIANT_PREFIXES[locale]}{text}" for locale, text in question.items()}
                if isinstance(question, dict)
                else f"{VARIANT_PREFIXES['en']}{question}"
            )
            variant["notes"] = f"Wording variant: {variant['notes']}"
            variants.append(variant)
    return [*cases, *variants]
