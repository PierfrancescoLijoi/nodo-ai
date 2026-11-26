"""Offline comparison of evidence-selection rules over dumped retrieval features.

    python eval/selection_study.py features.json

For every rule it prints, per language, citation precision and recall of the selected set against
the annotated evidence, and the accuracy of the "is there any evidence at all" decision.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np


def zs(values):
    a = np.asarray(values, dtype=float)
    return (a - a.mean()) / (a.std() + 1e-9)


def scores(row, mode):
    c = row["candidates"]
    cos = np.array([x["cos"] for x in c])
    rr = np.array([x["rerank"] for x in c])
    bm = np.array([0.0 if x["bm25_rank"] is None else 1 / (1 + x["bm25_rank"]) for x in c])
    if mode == "cos":
        return cos
    if mode == "rerank":
        return rr
    if mode == "hybrid":  # standardised cosine + reranker (+ lexical)
        return zs(cos) + zs(rr) + 0.5 * zs(bm)
    raise ValueError(mode)


def evaluate(rows, mode, ratio, floor, k_max=6):
    stat = defaultdict(lambda: [0, 0, 0, 0, 0, 0])  # tp, selected, expected, gate_ok, gate_n, forb
    for row in rows:
        s = scores(row, mode)
        order = np.argsort(-s)
        top = s[order[0]]
        chosen = []
        if top >= floor:
            chosen = [row["candidates"][i]["id"] for i in order[:k_max] if s[i] >= top - ratio]
        want = set(row["expected"])
        answerable = row["answerability"] != "unanswerable"
        st = stat[row["locale"]]
        st[0] += len(set(chosen) & want)
        st[1] += len(chosen)
        st[2] += len(want)
        st[3] += (bool(chosen) == answerable)
        st[4] += 1
        st[5] += len(set(chosen) & set(row["forbidden"]))
    return stat


def main() -> None:
    rows = json.loads(open(sys.argv[1]).read())
    best = []
    for mode in ("cos", "rerank", "hybrid"):
        lo, hi = (np.percentile([c[mode == "rerank" and "rerank" or "cos"] for r in rows for c in r["candidates"]], [5, 95]))
        for ratio in (0.5, 1, 1.5, 2, 3, 4, 100):
            for floor in np.linspace(-8, 6, 15) if mode != "cos" else np.linspace(0.6, 0.9, 16):
                stat = evaluate(rows, mode, ratio, floor)
                tp = sum(v[0] for v in stat.values()); sel = sum(v[1] for v in stat.values()); exp = sum(v[2] for v in stat.values())
                gate = sum(v[3] for v in stat.values()) / sum(v[4] for v in stat.values())
                p, r = tp / max(sel, 1), tp / max(exp, 1)
                best.append((2 * p * r / max(p + r, 1e-9), mode, ratio, round(float(floor), 2), round(p, 3), round(r, 3), round(gate, 3)))
    best.sort(reverse=True)
    print("f1 mode ratio floor precision recall gate")
    seen = set()
    for b in best:
        if b[1] in seen and sum(1 for x in seen if x == b[1]) >= 0:
            pass
        print(*b)
        if len([1 for x in best[: best.index(b) + 1] if x[1] == b[1]]) >= 3:
            seen.add(b[1])
        if len(seen) == 3:
            break


if __name__ == "__main__":
    main()
