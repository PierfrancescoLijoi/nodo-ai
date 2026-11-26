"""Dump per-candidate retrieval features (vector cosine, BM25 rank, cross-encoder score) as JSON.

    services/api/.venv/Scripts/python eval/retrieval_features.py out.json [embedding-model-folder]

Every authorized segment of the caller's workspace is scored, so selection rules can be compared
offline (eval/selection_study.py) without running the language model again.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden import expanded_cases  # noqa: E402,F401
from run_queries import ROOT, build_adapters, load_fixtures, pick_question  # noqa: E402

LOCALES = ("en", "it", "pl", "da")


def main() -> None:
    out = Path(sys.argv[1])
    cases = [json.loads(line) for line in (ROOT / "golden_set/cases.jsonl").read_text().splitlines()]
    meetings = json.loads((ROOT / "golden_set/fixtures/meetings.json").read_text())
    meetings = meetings if isinstance(meetings, list) else meetings["meetings"]
    rows = []
    with tempfile.TemporaryDirectory(prefix="feat-", dir=ROOT.parent / "tmp", ignore_cleanup_errors=True) as folder:
        _, adapters = build_adapters("local", Path(folder))
        if len(sys.argv) > 2:
            from nodo_api.adapters.local.embedder import LocalEmbedder

            adapters.retriever._embedder = LocalEmbedder(Path(sys.argv[2]))
        load_fixtures(adapters, meetings)
        r = adapters.retriever
        for locale in LOCALES:
            for case in cases:
                ws, user = case["workspace_id"], case["caller_user_id"]
                q = pick_question(case["question"], locale)
                rowids, matrix = r._matrix(ws, user)
                qv = np.asarray(r._embedder.embed_query(q), dtype=np.float32)[0]
                qv /= np.linalg.norm(qv)
                cos = matrix @ qv
                segs = r._load([int(x) for x in rowids])
                text_rank = {rid: i for i, rid in enumerate(r._text_hits(ws, user, q, None, None))}
                rerank = r._reranker.score(q, [s.text for s in segs]) if r._reranker else [0.0] * len(segs)
                rows.append(
                    {
                        "locale": locale,
                        "id": case["id"],
                        "category": case["category"],
                        "answerability": case["expected_answerability"],
                        "expected": case["expected_citation_segment_ids"],
                        "forbidden": case["forbidden_segment_ids"],
                        "candidates": [
                            {
                                "id": s.id,
                                "cos": float(cos[i]),
                                "bm25_rank": text_rank.get(int(rowids[i])),
                                "rerank": float(rerank[i]),
                                "meeting": s.meeting_id,
                            }
                            for i, s in enumerate(segs)
                        ],
                    }
                )
    out.write_text(json.dumps(rows))


if __name__ == "__main__":
    main()
