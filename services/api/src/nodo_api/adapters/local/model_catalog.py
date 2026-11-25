"""The local model catalog is descriptive and offline-safe.

It records only artifacts NODO is allowed to install.  Entries without an immutable SHA-256 are
shown to the owner but intentionally cannot be installed by the future in-app lifecycle API.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class HardwareTier(StrEnum):
    CPU = "cpu"
    GPU_6GB = "gpu_6gb"
    GPU_LARGE = "gpu_large"


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    id: str
    task: str
    name: str
    publisher: str
    version: str
    size_mb: int
    licence: str
    licence_url: str
    hardware: tuple[HardwareTier, ...]
    relative_paths: tuple[str, ...]
    url: str | None = None
    sha256: str | None = None

    @property
    def installable(self) -> bool:
        return self.url is not None and self.sha256 is not None

    def owned_paths(self, root: Path) -> tuple[Path, ...]:
        base = root.resolve()
        resolved = tuple((base / part).resolve() for part in self.relative_paths)
        if any(path != base and base not in path.parents for path in resolved):
            raise ValueError("catalog path escapes model root")
        return resolved


CATALOG: tuple[ModelCatalogEntry, ...] = (
    ModelCatalogEntry(
        "llama-cpp", "runtime", "llama.cpp server", "ggml-org", "b11823", 35, "MIT",
        "https://github.com/ggml-org/llama.cpp/blob/master/LICENSE", (HardwareTier.CPU, HardwareTier.GPU_6GB, HardwareTier.GPU_LARGE),
        ("llama.cpp",), "https://github.com/ggml-org/llama.cpp/releases/download/b11823/llama-b11823-bin-win-vulkan-x64.zip",
        "5f75e297498e1036a70f42f02b40bfdd0fe2bda0749985f7a59dabc624b88ab0",
    ),
    ModelCatalogEntry(
        "speaker-segmentation", "diarization", "Pyannote segmentation 3.0", "k2-fsa", "3.0", 7, "MIT",
        "https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE", (HardwareTier.CPU, HardwareTier.GPU_6GB, HardwareTier.GPU_LARGE),
        ("diarization",), "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    ),
    ModelCatalogEntry(
        "qwen3-4b-q5", "language", "Qwen3 4B Q5_K_M", "Qwen", "Qwen3-4B", 2900, "Apache-2.0",
        "https://huggingface.co/Qwen/Qwen3-4B-GGUF", (HardwareTier.GPU_6GB, HardwareTier.GPU_LARGE),
        (), None, None,
    ),
    ModelCatalogEntry(
        "faster-whisper-medium", "speech", "faster-whisper medium", "Systran", "medium", 1500, "MIT",
        "https://huggingface.co/Systran/faster-whisper-medium", (HardwareTier.CPU, HardwareTier.GPU_6GB, HardwareTier.GPU_LARGE),
        (), None, None,
    ),
)


def recommend(gpu_bytes: int | None) -> HardwareTier:
    if gpu_bytes is None or gpu_bytes < 6 * 1024**3:
        return HardwareTier.CPU
    if gpu_bytes < 12 * 1024**3:
        return HardwareTier.GPU_6GB
    return HardwareTier.GPU_LARGE


def recommended_entries(gpu_bytes: int | None) -> tuple[ModelCatalogEntry, ...]:
    tier = recommend(gpu_bytes)
    return tuple(entry for entry in CATALOG if tier in entry.hardware)


def installed(entry: ModelCatalogEntry, root: Path) -> bool:
    """An entry owns only explicit paths; an empty path list is never treated as installed."""
    paths = entry.owned_paths(root)
    return bool(paths) and all(path.exists() for path in paths)


def remove(entry: ModelCatalogEntry, root: Path) -> None:
    """Delete only catalog-owned paths after resolving them beneath the model root."""
    for path in entry.owned_paths(root):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def safe_archive_members(names: tuple[str, ...]) -> None:
    """Reject absolute paths and traversal before extracting a verified model archive."""
    for name in names:
        path = Path(name)
        if name.startswith(("/", "\\")) or path.is_absolute() or ".." in path.parts:
            raise ValueError("model archive contains an unsafe path")


def verify_sha256(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest() == expected.lower()
