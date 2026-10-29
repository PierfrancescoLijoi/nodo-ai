"""Small ONNX cross-encoder used as the final, bounded stage of local hybrid search."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from nodo_api.errors import ModelUnavailable

DEFAULT_MODEL = "Horizon-Labs/multilingual-reranker-small"
MODEL_FILES = (
    "onnx/model_quantized.onnx",
    "onnx/model_quint8_avx2.onnx",
    "onnx/model_qint8_avx512.onnx",
    "model.onnx",
)


class OnnxReranker:
    """A lazy, CPU-only cross-encoder with no PyTorch dependency."""

    def __init__(self, folder: Path | None, batch_size: int = 8, max_length: int = 384) -> None:
        self._folder = folder
        self._batch_size = max(1, batch_size)
        self._max_length = max(32, max_length)
        self._tokenizer = None
        self._session = None
        self._load_lock = threading.Lock()
        # Serial inference avoids several concurrent queries saturating every laptop CPU core.
        self._run_lock = threading.Lock()

    @staticmethod
    def installed(folder: Path | None) -> bool:
        return bool(
            folder
            and (folder / "tokenizer.json").is_file()
            and any((folder / name).is_file() for name in MODEL_FILES)
        )

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        self._load()
        scores: list[float] = []
        with self._run_lock:
            for start in range(0, len(documents), self._batch_size):
                batch = documents[start : start + self._batch_size]
                encoded = self._tokenizer.encode_batch([(query, text) for text in batch])
                inputs = self._inputs(encoded)
                output = self._session.run(None, inputs)[0]
                scores.extend(np.asarray(output, dtype=np.float32).reshape(-1).tolist())
        return scores

    def _load(self) -> None:
        if self._session is not None:
            return
        if not self.installed(self._folder):
            raise ModelUnavailable
        with self._load_lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            assert self._folder is not None
            model = next(
                self._folder / name for name in MODEL_FILES if (self._folder / name).is_file()
            )
            tokenizer = Tokenizer.from_file(str(self._folder / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=self._max_length)
            padding = next(
                (
                    (token, token_id)
                    for token in ("[PAD]", "<pad>")
                    if (token_id := tokenizer.token_to_id(token)) is not None
                ),
                None,
            )
            if padding is None:
                raise ModelUnavailable
            pad_token, pad_id = padding
            tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = 0
            self._tokenizer = tokenizer
            self._session = ort.InferenceSession(
                str(model), sess_options=options, providers=["CPUExecutionProvider"]
            )

    def _inputs(self, encoded) -> dict[str, np.ndarray]:
        available = {item.name for item in self._session.get_inputs()}
        values = {
            "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
            "attention_mask": np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
            "token_type_ids": np.asarray([item.type_ids for item in encoded], dtype=np.int64),
        }
        return {name: value for name, value in values.items() if name in available}
