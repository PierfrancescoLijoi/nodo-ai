"""Local multilingual embeddings (E5). The counterpart of the Azure OpenAI embedding deployment.

E5 models are trained with a prefix that says whether a text is a question or a passage; without
it the vectors work noticeably worse, so the retriever calls `embed_query` and `embed_passages`
instead of the plain `embed` the Azure adapter offers. Runs on the CPU: a base model embeds a
transcript fast enough, and the GPU stays free for speech and language models.
"""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_MODEL = "intfloat/multilingual-e5-base"
BATCH_SIZE = 32


class LocalEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        self._name = model_name
        self._device = device
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self._name, device=self._device, local_files_only=True
                )
            return self._model

    def _encode(self, prefix: str, texts: list[str]) -> list[list[float]]:
        vectors = self._load().encode(
            [prefix + t for t in texts],
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode("passage: ", texts)

    def embed_query(self, text: str) -> list[list[float]]:
        return self._encode("query: ", [text])

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embed_passages(texts)

    @property
    def dimensions(self) -> int:
        return int(self._load().get_sentence_embedding_dimension())
