"""Retrieval-only probe: ranks of the annotated evidence per language, with no language model.

    services/api/.venv/Scripts/python eval/retrieval_probe.py [reranker]

Shows how well search alone separates the annotated segments from the rest, and how far the best
score of an answerable question sits above that of an unanswerable one: the two facts an evidence
selection and answerability gate can be built on.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden import expanded_cases  # noqa: E402
from run_queries import ROOT, build_adapters, load_fixtures, pick_question  # noqa: E402

LOCALES = ("en", "it", "pl", "da")


def main() -> None:
    cases = [json.loads(line) for line in (ROOT / "golden_set/cases.jsonl").read_text().splitlines()]
    meetings = json.loads((ROOT / "golden_set/fixtures/meetings.json").read_text())
    meetings = meetings if isinstance(meetings, list) else meetings["meetings"]
    with tempfile.TemporaryDirectory(prefix="probe-", dir=ROOT.parent / "tmp", ignore_cleanup_errors=True) as folder:
        _, adapters = build_adapters("local", Path(folder))
        load_fixtures(adapters, meetings)
        retriever = adapters.retriever
        for locale in LOCALES:
            hits_at = {1: 0, 3: 0, 5: 0}
            expected_total = reciprocal = 0.0
            supported_top, other_top = [], []
            n_supported = 0
            dump = []
            for case in cases:
                found = retriever.search(
                    case["workspace_id"], case["caller_user_id"], pick_question(case["question"], locale), None, 20
                )
                ids = [s.id for s in found]
                rr = retriever._reranker
                if rr is not None and found:
                    sc = rr.score(pick_question(case["question"], locale), [s.text for s in found])
                    pos = [sc[i] for i, s in enumerate(found) if s.id in case["expected_citation_segment_ids"]]
                    neg = [sc[i] for i, s in enumerate(found) if s.id not in case["expected_citation_segment_ids"]]
                    dump.append((locale, case["id"], case["category"], case["expected_answerability"], [round(float(x), 3) for x in pos], [round(float(x), 3) for x in sorted(neg, reverse=True)[:4]]))
                want = case["expected_citation_segment_ids"]
                if want:
                    n_supported += 1
                    for k in hits_at:
                        hits_at[k] += len(set(ids[:k]) & set(want)) / min(len(want), k)
                    ranks = [ids.index(w) + 1 for w in want if w in ids]
                    reciprocal += 1 / min(ranks) if ranks else 0
            print(
                locale,
                {f"recall@{k}": round(v / n_supported, 3) for k, v in hits_at.items()},
                "MRR",
                round(reciprocal / n_supported, 3),
            )
            if locale == "en" or "--dump" in sys.argv:
                for row in dump:
                    print(*row)


if __name__ == "__main__":
    main()
