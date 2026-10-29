"""Convert the E5 embedding model to a small ONNX file the engine can run without PyTorch.

    python tools/local-setup/export_embedding_onnx.py [model name]

One-time step, run after `install_models.py` (the model must already be in the Hugging Face cache).
It needs PyTorch and transformers, which the engine itself does not: the engine then runs the model
with onnxruntime and the tokenizers library only. Output goes to
%LOCALAPPDATA%/NODO/models/embeddings/<model name>/: model.int8.onnx (dynamic int8 quantization,
about a quarter of the size and faster on the CPU) and tokenizer.json.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[2] / "services" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))


def main() -> int:
    from nodo_api.adapters.local.embedder import DEFAULT_MODEL, onnx_folder
    from nodo_api.adapters.local.models import data_dir

    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    target = onnx_folder(data_dir() / "models", name)
    target.mkdir(parents=True, exist_ok=True)
    # PyTorch's exporter prints Unicode status marks; legacy Windows consoles default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    import torch
    from huggingface_hub import hf_hub_download
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name, local_files_only=True)
    model = AutoModel.from_pretrained(
        name, local_files_only=True, attn_implementation="eager"
    ).eval()
    sample = tokenizer(
        ["passage: hello world", "query: a longer example sentence"],
        padding=True,
        return_tensors="pt",
    )
    full = target / "model.onnx"

    class Wrapped(torch.nn.Module):  # explicit keywords: positional order differs between versions
        def __init__(self, inner: torch.nn.Module) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask):
            return self.inner(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

    wrapped = Wrapped(model).eval()
    with torch.inference_mode():
        torch.onnx.export(
            wrapped,
            (sample["input_ids"], sample["attention_mask"]),
            str(full),
            input_names=["input_ids", "attention_mask"],
            output_names=["last_hidden_state"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "tokens"},
                "attention_mask": {0: "batch", 1: "tokens"},
                "last_hidden_state": {0: "batch", 1: "tokens"},
            },
            opset_version=18,
            dynamo=True,
        )
    quantize_dynamic(str(full), str(target / "model.int8.onnx"), weight_type=QuantType.QInt8)
    full.unlink()
    for leftover in target.glob("*.data"):
        leftover.unlink()
    source = Path(hf_hub_download(name, "tokenizer.json", local_files_only=True))
    shutil.copy(source, target / "tokenizer.json")
    print(f"written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
