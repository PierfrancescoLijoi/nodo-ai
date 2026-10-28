"""Local multilingual embeddings (E5). The counterpart of the Azure OpenAI embedding deployment.

E5 models are trained with a prefix that says whether a text is a question or a passage; without
it the vectors work noticeably worse, so the retriever calls `embed_query` and `embed_passages`
instead of the plain `embed` the Azure adapter offers. Runs on the CPU: a base model embeds a
transcript fast enough, and the GPU stays free for speech and language models.

The model runs as an int8 ONNX file with `onnxruntime` and the `tokenizers` library, with mean
pooling over the tokens and unit length (what the E5 model card prescribes). No PyTorch, no
transformers: that import chain pulls pandas, pyarrow and scikit-learn, and crashed the whole engine
process (an access violation inside pyarrow) on this machine. The file is made once from the
downloaded model by tools/local-setup/export_embedding_onnx.py.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from nodo_api.errors import ModelUnavailable

DEFAULT_MODEL = "intfloat/multilingual-e5-base"
MODEL_FILE = "model.int8.onnx"
TOKENIZER_FILE = "tokenizer.json"
BATCH_SIZE = 32
MAX_TOKENS = 512
THREADS = 4


def onnx_folder(models_dir: Path, name: str = DEFAULT_MODEL) -> Path:
    return models_dir / "embeddings" / name.split("/")[-1]


class LocalEmbedder:
    def __init__(self, folder: Path | None) -> None:
        self._folder = folder
        self._parts: tuple[Any, Any] | None = None  # tokenizer, onnx session
        self._lock = threading.Lock()

    @staticmethod
    def installed(folder: Path | None) -> bool:
        return (
            folder is not None
            and (folder / MODEL_FILE).exists()
            and (folder / TOKENIZER_FILE).exists()
        )

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._parts is None:
                if not self.installed(self._folder):
                    raise ModelUnavailable
                import onnxruntime as ort
                from tokenizers import Tokenizer

                assert self._folder is not None
                tokenizer = Tokenizer.from_file(str(self._folder / TOKENIZER_FILE))
                tokenizer.enable_truncation(max_length=MAX_TOKENS)
                tokenizer.enable_padding(
                    pad_id=tokenizer.token_to_id("<pad>") or 1, pad_token="<pad>"
                )
                options = ort.SessionOptions()
                options.intra_op_num_threads = THREADS
                session = ort.InferenceSession(
                    str(self._folder / MODEL_FILE), options, providers=["CPUExecutionProvider"]
                )
                self._parts = (tokenizer, session)
            return self._parts

    def _encode(self, prefix: str, texts: list[str]) -> list[list[float]]:
        tokenizer, session = self._load()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = tokenizer.encode_batch([prefix + t for t in texts[start : start + BATCH_SIZE]])
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            hidden = session.run(None, {"input_ids": ids, "attention_mask": mask})[0]
            weights = mask[:, :, None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)
            vectors += pooled.tolist()
        return vectors

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode("passage: ", texts)

    def embed_query(self, text: str) -> list[list[float]]:
        return self._encode("query: ", [text])

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embed_passages(texts)
