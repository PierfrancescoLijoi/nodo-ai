"""Where the local models live, and how they share one GPU.

A laptop GPU holds one big model at a time (Whisper medium needs about 2 GB, a 4B language model
with a 16k context about 5 GB, on a 6 GB card), so the GPU is leased: speech, then language model,
never both. The language model runs as `llama-server` (llama.cpp), which speaks the same chat API
as Azure OpenAI, so the Azure adapters work against it unchanged (ADR-014).
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("nodo_models")

HF_CACHE = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
START_TIMEOUT_SECONDS = 180
IDLE_UNLOAD_SECONDS = 600


def data_dir() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or Path.home() / ".local/share"
    )
    return Path(base) / "NODO"


def find_gguf(repo_dir: str, pattern: str = "*.gguf") -> Path | None:
    """The first matching file of a model already downloaded into the Hugging Face cache."""
    for path in sorted((HF_CACHE / repo_dir / "snapshots").glob(f"*/{pattern}")):
        return path.resolve()
    return None


def find_snapshot(repo_dir: str, required: str) -> Path | None:
    """Newest cached Hugging Face snapshot containing the required relative file."""
    snapshots = sorted(
        (HF_CACHE / repo_dir / "snapshots").glob("*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for snapshot in snapshots:
        if (snapshot / required).is_file():
            return snapshot.resolve()
    return None


@dataclass(frozen=True, slots=True)
class LocalModelPaths:
    """Everything the local engine needs on disk. Missing pieces are reported, never downloaded."""

    llama_server: Path | None
    llm_gguf: Path | None
    diarization_dir: Path | None
    embedding_dir: Path | None = None
    reranker_dir: Path | None = None
    whisper_model: str = "medium"
    embedding_model: str = "intfloat/multilingual-e5-base"
    ffmpeg: str | None = None

    @classmethod
    def discover(cls, models_dir: Path | None = None) -> LocalModelPaths:
        root = models_dir or data_dir() / "models"
        server = root / "llama.cpp" / "llama-server.exe"
        server = server if server.exists() else root / "llama.cpp" / "llama-server"
        diarization = root / "diarization"
        embeddings = root / "embeddings" / "multilingual-e5-base"
        reranker = find_snapshot(
            "models--Horizon-Labs--multilingual-reranker-small",
            "onnx/model_quantized.onnx",
        )
        import shutil

        return cls(
            llama_server=server if server.exists() else None,
            llm_gguf=find_gguf("models--Qwen--Qwen3-4B-GGUF", "Qwen3-4B-*.gguf"),
            diarization_dir=diarization if diarization.exists() else None,
            embedding_dir=embeddings if (embeddings / "model.int8.onnx").exists() else None,
            reranker_dir=reranker,
            ffmpeg=os.environ.get("NODO_FFMPEG") or shutil.which("ffmpeg"),
        )

    def missing(self) -> list[str]:
        """Human-readable list of what is not installed, for the setup screen."""
        gaps = []
        if self.llama_server is None:
            gaps.append("llama.cpp server")
        if self.llm_gguf is None:
            gaps.append("language model (GGUF)")
        if self.diarization_dir is None:
            gaps.append("speaker separation models")
        if self.embedding_dir is None:
            gaps.append("embedding model (run tools/local-setup/export_embedding_onnx.py)")
        if self.ffmpeg is None:
            gaps.append("ffmpeg")
        return gaps


class GpuArbiter:
    """One heavy model on the GPU at a time. `lease("llm")` unloads whatever else is loaded."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._holder: str | None = None
        self._unloaders: dict[str, Callable[[], None]] = {}

    def register(self, name: str, unload: Callable[[], None]) -> None:
        self._unloaders[name] = unload

    @contextmanager
    def lease(self, name: str) -> Iterator[None]:
        with self._lock:
            if self._holder not in (None, name):
                logger.info("gpu_switch from=%s to=%s", self._holder, name)
                self._unloaders[self._holder]()
            self._holder = name
            yield

    def release(self, name: str) -> None:
        with self._lock:
            if self._holder == name:
                self._unloaders[name]()
                self._holder = None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LlamaServer:
    """A llama.cpp server for one GGUF model, started when first needed and stopped when idle."""

    executable: Path
    model: Path
    pid_file: Path
    context: int = 16384
    gpu_layers: int = 99
    extra_args: tuple[str, ...] = ()
    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _port: int = field(default=0, init=False)
    _idle: threading.Timer | None = field(default=None, init=False, repr=False)
    _guard: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    def ensure_started(self) -> str:
        with self._guard:
            if self._process is None or self._process.poll() is not None:
                self._start()
            self._touch()
            return self.base_url

    def _start(self) -> None:
        self._kill_stale()
        self._port = free_port()
        command = [
            str(self.executable),
            "-m",
            str(self.model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "-c",
            str(self.context),
            "-ngl",
            str(self.gpu_layers),
            "--jinja",
            "--no-webui",
            *self.extra_args,
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            cwd=str(self.executable.parent),
        )
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(self._process.pid))
        self._wait_ready()

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError("The language model server stopped while starting")
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"http://127.0.0.1:{self._port}/health", timeout=2).status_code == 200:
                    return
            time.sleep(0.5)
        self.stop()
        raise RuntimeError("The language model server did not become ready in time")

    def _kill_stale(self) -> None:
        """A server left by a crashed engine would hold the GPU: stop it before starting ours."""
        if not self.pid_file.exists():
            return
        with contextlib.suppress(ValueError, OSError):
            pid = int(self.pid_file.read_text())
            if _is_our_server(pid, self.executable.name):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False
                )
        self.pid_file.unlink(missing_ok=True)

    def _touch(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        self._idle = threading.Timer(IDLE_UNLOAD_SECONDS, self.stop)
        self._idle.daemon = True
        self._idle.start()

    def stop(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.pid_file.unlink(missing_ok=True)


def _is_our_server(pid: int, executable_name: str) -> bool:
    """True only if the process with this id really is the llama server (ids are reused)."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return executable_name.lower() in result.stdout.lower()
