"""MRR / recall@3 of each scoring signal per language, from dumped features.

    python eval/ranking_study.py features.json
"""

import json
import sys
from collections import defaultdict

import numpy as np

rows = json.load(open(sys.argv[1]))
acc = defaultdict(list)
for r in rows:
    if not r["expected"]:
        continue
    c = r["candidates"]
    sig = {
        "cos": np.array([x["cos"] for x in c]),
        "rerank": np.array([x["rerank"] for x in c]),
        "bm25": np.array([0.0 if x["bm25_rank"] is None else 1 / (1 + x["bm25_rank"]) for x in c]),
    }
    z = lambda a: (a - a.mean()) / (a.std() + 1e-9)  # noqa: E731
    sig["cos+rerank"] = z(sig["cos"]) + z(sig["rerank"])
    for name, s in sig.items():
        order = [c[i]["id"] for i in np.argsort(-s)]
        ranks = [order.index(e) + 1 for e in r["expected"]]
        acc[(name, r["locale"])].append((1 / min(ranks), len(set(order[:3]) & set(r["expected"])) / min(3, len(r["expected"]))))
for (name, loc), v in sorted(acc.items()):
    print(f"{name:11s} {loc}  MRR {np.mean([a for a,_ in v]):.3f}  R@3 {np.mean([b for _,b in v]):.3f}")
