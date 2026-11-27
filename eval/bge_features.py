"""Ranking features from BGE-M3 (dense) and BGE-reranker-v2-m3, for comparison with E5.

    services/api/.venv/Scripts/python eval/bge_features.py out.json
Same JSON shape as retrieval_features.py ("cos" = BGE-M3 cosine, "rerank" = cross-encoder logit).
Research harness only: it needs PyTorch, which the shipped engine does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parent
LOCALES = ("en", "it", "pl", "da")
dev = "cuda" if torch.cuda.is_available() else "cpu"
cases = [json.loads(l) for l in (ROOT / "golden_set/cases.jsonl").read_text().splitlines()]
d = json.loads((ROOT / "golden_set/fixtures/meetings.json").read_text())
meetings = d if isinstance(d, list) else d["meetings"]
segs = [(m["workspace_id"], s["id"], s["text"], m["id"]) for m in meetings for s in m["segments"] if not s.get("tombstoned")]

tok = AutoTokenizer.from_pretrained("BAAI/bge-m3", local_files_only=True)
enc = AutoModel.from_pretrained("BAAI/bge-m3", local_files_only=True, torch_dtype=torch.float16).to(dev).eval()
rtok = AutoTokenizer.from_pretrained("BAAI/bge-reranker-v2-m3", local_files_only=True)
rer = AutoModelForSequenceClassification.from_pretrained("BAAI/bge-reranker-v2-m3", local_files_only=True, torch_dtype=torch.float16).to(dev).eval()


@torch.no_grad()
def embed(texts):
    b = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to(dev)
    v = enc(**b).last_hidden_state[:, 0].float()
    return torch.nn.functional.normalize(v, dim=-1).cpu().numpy()


@torch.no_grad()
def rerank(q, docs):
    b = rtok([q] * len(docs), docs, padding=True, truncation=True, max_length=512, return_tensors="pt").to(dev)
    return rer(**b).logits.view(-1).float().cpu().numpy()


P = embed([s[2] for s in segs])
rows = []
for loc in LOCALES:
    for c in cases:
        q = c["question"][loc] if isinstance(c["question"], dict) else c["question"]
        idx = [i for i, s in enumerate(segs) if s[0] == c["workspace_id"]]
        qv = embed([q])[0]
        cos = P[idx] @ qv
        rr = rerank(q, [segs[i][2] for i in idx])
        rows.append({
            "locale": loc, "id": c["id"], "category": c["category"], "answerability": c["expected_answerability"],
            "expected": c["expected_citation_segment_ids"], "forbidden": c["forbidden_segment_ids"],
            "candidates": [{"id": segs[i][1], "cos": float(cos[k]), "bm25_rank": None, "rerank": float(rr[k]), "meeting": segs[i][3]} for k, i in enumerate(idx)],
        })
Path(sys.argv[1]).write_text(json.dumps(rows))
