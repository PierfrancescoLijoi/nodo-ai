"""The local engine with stand-ins for the models, for the browser tests of the web app.

    python scripts/e2e_engine.py --web ../../apps/web/out --port 8799

It is the real local backend (SQLite, the job queue, the HTTP API, the file server for the exported
web app); only the models are replaced by the deterministic fakes the unit tests use, so a
recording is "transcribed" in a moment and no model has to be installed. The access key is fixed so
a test can open the page the way the desktop launcher does: http://127.0.0.1:PORT/#token=KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path

import numpy as np
import uvicorn

from nodo_api.adapters.memory import MemorySpeech
from nodo_api.adapters.memory.adapters import MockLlm
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.config import Settings
from nodo_api.main import create_app
from nodo_api.services.entity_index import EntityIndexer
from nodo_api.services.ingestion import IngestionService
from nodo_api.worker import Worker

TOKEN = "e2e-local-engine-key-0123456789-0123456789"
VECTOR_SIZE = 64


class HashEmbedder:
    """Words hashed into buckets: texts that share words get similar vectors, with no model."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = np.zeros(VECTOR_SIZE)
            for word in text.lower().split():
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % VECTOR_SIZE] += 1
            vectors.append(vector.tolist())
        return vectors


def build_app(data: Path, web: Path | None, port: int):
    settings = Settings(
        environment="prod",
        backend="local",
        auth_mode="local",
        local_token=TOKEN,
        local_data_dir=data,
        local_public_url=f"http://127.0.0.1:{port}",
        local_web_dir=web,
        attestation_hmac_key="e2e-attestation-key-0123456789-0123456789",
    )
    app = create_app(settings)
    container = app.state.container
    embedder = HashEmbedder()
    container.retriever._embedder = embedder
    container.query_service._llm = MockLlm()
    container.runtime.loop._worker = Worker(
        IngestionService(
            container.meetings,
            container.transcripts,
            container.blobs,
            container.retriever,
            MemorySpeech(),
            ("en-GB",),
            KeywordExtractor(),
            container.graph,
            entity_indexer=EntityIndexer(container.graph, embedder),
        ),
        exports=container.export_service,
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--web", type=Path, default=None)
    parser.add_argument("--data", type=Path, default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nodo-e2e-") as scratch:
        data = args.data or Path(scratch)
        uvicorn.run(
            build_app(data, args.web, args.port),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
