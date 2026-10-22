"""Run the whole local pipeline on one recording and print what comes out and how long it took.

    python tools/local-check/check_pipeline.py <audio file> [locale]

Speech (Whisper + speaker separation) -> segments -> extraction (language model) -> validated
graph -> a question answered from the search index. Needs the local models (see ADR-014); nothing
leaves the machine.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.embedder import LocalEmbedder
from nodo_api.adapters.local.graph import SqliteGraphStore
from nodo_api.adapters.local.llm import LocalExtractor, LocalLlm
from nodo_api.adapters.local.models import GpuArbiter, LlamaServer, LocalModelPaths, data_dir
from nodo_api.adapters.local.search import SqliteRetriever
from nodo_api.adapters.local.speech import LocalSpeechToText
from nodo_api.domain.graph import MeetingRef, validate_extraction
from nodo_api.domain.models import Locale
from nodo_api.services.extraction import ChunkedExtractor
from nodo_api.services.ingestion import build_segments
from datetime import UTC, datetime

CANDIDATES = ("en-GB", "pl-PL", "it-IT", "da-DK")
WS, OWNER = "local", "me"


def timed(label: str, fn):
    start = time.perf_counter()
    result = fn()
    print(f"[{time.perf_counter() - start:6.1f} s] {label}")
    return result


def main() -> None:
    audio = sys.argv[1]
    locale = Locale(sys.argv[2]) if len(sys.argv) > 2 else Locale.EN
    models = LocalModelPaths.discover()
    print("missing:", models.missing() or "nothing")
    arbiter = GpuArbiter()
    server = LlamaServer(models.llama_server, models.llm_gguf, data_dir() / "llama.pid")
    speech = LocalSpeechToText(models, arbiter)
    extractor = ChunkedExtractor(LocalExtractor(server, arbiter), max_tokens=6000)
    llm = LocalLlm(server, arbiter)
    db = LocalDatabase(Path(tempfile.mkdtemp()) / "check.db")
    graph, embedder = SqliteGraphStore(db), LocalEmbedder(models.embedding_dir)
    retriever = SqliteRetriever(db, embedder, min_similarity=0.78)

    phrases = timed("speech to text + speakers", lambda: speech.transcribe(audio, CANDIDATES))
    segments = build_segments("m1", phrases, locale, None)
    print(f"{len(segments)} segments, speakers {sorted({s.speaker_id for s in segments})}")
    for s in segments[:6]:
        print(f"  {s.speaker_id} {s.start_ms / 1000:6.1f}s  {s.text[:90]}")

    timed("index", lambda: retriever.index(WS, OWNER, segments))
    raw = timed("extraction", lambda: extractor.extract(segments, locale, (), {}))
    clean = validate_extraction(raw, frozenset(s.id for s in segments), {})
    print("summary:", clean.summary)
    for e in clean.entities:
        print(f"  {e.type.value:9} {e.label}")
    for r in clean.relations:
        print(f"  {r.source} -{r.kind.value}-> {r.target}")
    graph.upsert_extraction(WS, OWNER, MeetingRef("m1", "check", datetime.now(UTC)), clean)

    question = "What did we decide about the queue?" if locale is Locale.EN else "Cosa abbiamo deciso sulla coda?"
    evidence = timed("search", lambda: retriever.search(WS, OWNER, question, None, 6))
    answer = timed("answer", lambda: llm.answer(WS, question, locale, evidence, graph.neighborhood(
        WS, OWNER, [e.id for e in graph.overview(WS, OWNER, 5)], depth=1, node_limit=30, fanout=10,
        include_proposed=True, as_of=None)))
    print("answer:", answer.answerability.value, "|", answer.answer)
    for c in answer.citations:
        print("  cite", c.speaker_id, c.quote[:80])
    server.stop()


if __name__ == "__main__":
    main()
