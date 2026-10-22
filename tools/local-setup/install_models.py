"""Install what the local engine needs: model runtimes and models, with sizes shown and checksums checked.

    python tools/local-setup/install_models.py [--yes] [--only NAME ...] [--list]

Nothing is downloaded without a yes. Files that are already present and correct are skipped. The
GitHub files are pinned by SHA-256; the Hugging Face models are fetched into the Hugging Face cache
(which verifies them) and are used from there, offline, by the engine.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from nodo_api.adapters.local.models import data_dir

CHUNK = 1 << 20


@dataclass(frozen=True)
class Download:
    name: str
    what: str
    url: str
    sha256: str
    size_mb: int
    target: str  # relative to the models folder
    unpack: str | None = None  # "zip" or "tar.bz2"


@dataclass(frozen=True)
class HuggingFace:
    name: str
    what: str
    repo: str
    size_mb: int
    patterns: tuple[str, ...] = ("*",)


GITHUB = (
    Download(
        "llama.cpp",
        "language model server (llama.cpp b11823, Vulkan build, MIT)",
        "https://github.com/ggml-org/llama.cpp/releases/download/b11823/llama-b11823-bin-win-vulkan-x64.zip",
        "5f75e297498e1036a70f42f02b40bfdd0fe2bda0749985f7a59dabc624b88ab0",
        35,
        "llama.cpp",
        "zip",
    ),
    Download(
        "speaker-segmentation",
        "who-speaks-when model (pyannote segmentation 3.0 as ONNX, MIT)",
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
        7,
        "diarization",
        "tar.bz2",
    ),
    Download(
        "speaker-embedding",
        "voice fingerprint model for telling speakers apart within one recording (3D-Speaker ERes2Net)",
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx",
        "c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5",
        26,
        "diarization/3dspeaker_eres2net.onnx",
    ),
)
HUB = (
    HuggingFace("whisper", "speech to text (faster-whisper medium)", "Systran/faster-whisper-medium", 1500),
    HuggingFace("embeddings", "search by meaning (multilingual E5 base)", "intfloat/multilingual-e5-base", 1100,
                ("*.json", "*.safetensors", "*.model", "1_Pooling/*", "tokenizer*")),
    HuggingFace("language-model", "answers and extraction (Qwen3 4B, Q5_K_M)", "Qwen/Qwen3-4B-GGUF", 2900,
                ("Qwen3-4B-Q5_K_M.gguf",)),
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def fetch(item: Download, models: Path) -> None:
    folder = models / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / item.url.rsplit("/", 1)[1]
    with urlopen(item.url) as reply, archive.open("wb") as out:  # noqa: S310 - fixed https URLs above
        while block := reply.read(CHUNK):
            out.write(block)
    if item.sha256 and sha256_of(archive) != item.sha256:
        archive.unlink()
        raise SystemExit(f"{item.name}: the downloaded file does not match its checksum; removed")
    target = models / item.target
    if item.unpack == "zip":
        with zipfile.ZipFile(archive) as bundle:
            _check_members([m for m in bundle.namelist()])
            bundle.extractall(target)
    elif item.unpack == "tar.bz2":
        with tarfile.open(archive, "r:bz2") as bundle:
            _check_members(bundle.getnames())
            bundle.extractall(target, filter="data")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        archive.replace(target)


def _check_members(names: list[str]) -> None:
    for name in names:
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise SystemExit("The archive contains an unsafe path; nothing was extracted")


def present(item: Download, models: Path) -> bool:
    target = models / item.target
    if item.unpack is None:
        return target.exists() and (not item.sha256 or sha256_of(target) == item.sha256)
    return target.exists() and any(target.iterdir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="do not ask before downloading")
    parser.add_argument("--list", action="store_true", help="only show what would be installed")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    models = data_dir() / "models"

    todo: list[tuple[str, int, str]] = []
    for item in GITHUB:
        if args.only and item.name not in args.only:
            continue
        status = "installed" if present(item, models) else "missing"
        print(f"{item.name:22} {item.size_mb:5} MB  {status:9} {item.what}")
        if status == "missing":
            todo.append((item.name, item.size_mb, item.url))
    for hub in HUB:
        if args.only and hub.name not in args.only:
            continue
        print(f"{hub.name:22} {hub.size_mb:5} MB  {'(checked by the hub cache)':9} {hub.what} <- {hub.repo}")
        todo.append((hub.name, hub.size_mb, hub.repo))
    if args.list or not todo:
        return 0
    total = sum(size for _, size, _ in todo)
    print(f"\nTo download: {total} MB from github.com and huggingface.co. No account is needed.")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
        return 1
    for item in GITHUB:
        if (not args.only or item.name in args.only) and not present(item, models):
            print(f"downloading {item.name} ...")
            fetch(item, models)
    for hub in HUB:
        if not args.only or hub.name in args.only:
            from huggingface_hub import snapshot_download

            print(f"fetching {hub.repo} ...")
            snapshot_download(hub.repo, allow_patterns=list(hub.patterns))
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
