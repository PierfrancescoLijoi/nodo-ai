"""Measure document import on the local engine against the annotated document set (Task 16).

    NODO_TEST_LOCAL_MODELS=1 services/api/.venv/Scripts/python eval/measure_import.py [gpu] [cpu]

Each file in `eval/documents/files` goes through `SourceImportService` exactly as an upload does
(create, upload, complete, process) on a fresh local engine (SQLite, the E5 embedder, the
document parser subprocess, Qwen3 on llama.cpp). It prints, per device pass:

- seconds per 50 pages for parsing (sniff, limits and parse), indexing (E5 on the CPU) and
  extraction (the language model on the GPU, or on the CPU with the GPU hidden). A page is
  3 000 characters of parsed text (`PAGE_CHARS`), so formats without pages compare;
- peak memory of this process and of the language model server;
- entity and relation precision and recall against `eval/documents/expected.jsonl`, next to the
  same measures for meeting extraction on the golden-set transcripts
  (`eval/golden_set/fixtures/meetings.json`, annotated in `eval/documents/meetings_expected.jsonl`),
  run the way the ingestion service runs it (owner-named speakers, then validation).

Scoring: types person, project, system, decision and action are scored (topics are not). A
predicted entity matches an annotation of an allowed type when one of its `match` terms appears in
the normalised label. Matching a required annotation is a true positive; matching an optional one
is ignored; anything else, including a second match of the same annotation, is a false positive.
Relations of the annotated kinds count when kind and both endpoints match.

Nothing is downloaded and nothing leaves the machine: the models must already be installed
(`LocalModelPaths.discover`). The machine is described generically (CPU, cores, GPU, RAM), never
by host or user name, and model files by file name only.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DOCUMENTS = ROOT / "documents"
PAGE_CHARS = 3000
SCORED = frozenset({"person", "project", "system", "decision", "action"})
RELATION_KINDS = frozenset({"ASSIGNED_TO", "PROPOSED_BY", "DEPENDS_ON"})
WORKSPACE, OWNER = "workspace-eval", "user-eval"


def machine() -> dict[str, Any]:
    import psutil

    cpu = platform.processor()
    if sys.platform == "win32":
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            cpu = str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
    elif Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu,
        "cores": psutil.cpu_count(logical=False),
        "threads": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "gpu": gpu_name(),
    }


def gpu_name() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out.splitlines()[0] if out else None


def model_files(models: Any) -> dict[str, str | None]:
    from nodo_api.adapters.local.models import HF_CACHE

    gguf = sorted(
        (HF_CACHE / "models--Qwen--Qwen3-4B-GGUF" / "snapshots").glob("*/Qwen3-4B-*.gguf")
    )
    return {
        "language_model": gguf[0].name if gguf else None,
        "embedding": models.embedding_dir.name if models.embedding_dir else None,
        "llama_server": models.llama_server.name if models.llama_server else None,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------------------- scoring


def score(entities: Any, relations: Any, expected: dict[str, Any]) -> Counter:
    from nodo_api.domain.graph import normalize_label

    def types(item: dict[str, Any]) -> set[str]:
        kind = item["type"]
        return {kind} if isinstance(kind, str) else set(kind)

    def hits(entity: Any, item: dict[str, Any]) -> bool:
        label = normalize_label(entity.label)
        terms = (normalize_label(term) for term in item["match"])
        return entity.type.value in types(item) and any(t and t in label for t in terms)

    wanted = expected["entities"]
    counts: Counter = Counter(required=sum(not w.get("optional") for w in wanted))
    taken: set[int] = set()
    label_of: dict[str, str] = {}
    for entity in entities:
        if entity.type.value not in SCORED:
            continue
        found = [i for i, w in enumerate(wanted) if hits(entity, w)]
        fresh = [i for i in found if not wanted[i].get("optional") and i not in taken]
        optional = [i for i in found if wanted[i].get("optional")]
        if fresh:
            taken.add(fresh[0])
            label_of[entity.key] = wanted[fresh[0]]["label"]
            counts["tp"] += 1
        elif optional:
            label_of[entity.key] = wanted[optional[0]]["label"]
        else:
            if found:
                label_of[entity.key] = wanted[found[0]]["label"]
            counts["fp"] += 1
    gold = {(r["kind"], r["source"], r["target"]) for r in expected["relations"]}
    counts["rel_required"] = len(gold)
    seen: set[tuple[str, str, str]] = set()
    for relation in relations:
        if relation.kind.value not in RELATION_KINDS:
            continue
        triple = (
            relation.kind.value,
            label_of.get(relation.source, ""),
            label_of.get(relation.target, ""),
        )
        if triple in gold and triple not in seen:
            seen.add(triple)
            counts["rel_tp"] += 1
        else:
            counts["rel_fp"] += 1
    return counts


def rates(counts: Counter) -> dict[str, float | None]:
    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "entity_precision": ratio(counts["tp"], counts["tp"] + counts["fp"]),
        "entity_recall": ratio(counts["tp"], counts["required"]),
        "relation_precision": ratio(counts["rel_tp"], counts["rel_tp"] + counts["rel_fp"]),
        "relation_recall": ratio(counts["rel_tp"], counts["rel_required"]),
    }


# --------------------------------------------------------------------------------------- engine


class Timed:
    """Times one method of an adapter and forwards everything else."""

    def __init__(self, inner: Any, method: str, seconds: Counter, bucket: str, on_call=None):
        self._inner, self._method, self._seconds, self._bucket = inner, method, seconds, bucket
        self._on_call = on_call

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name != self._method:
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            if self._on_call is not None:
                self._on_call(*args, **kwargs)
            start = time.perf_counter()
            try:
                return target(*args, **kwargs)
            finally:
                self._seconds[self._bucket] += time.perf_counter() - start

        return call


def build_engine(data_dir: Path):
    from nodo_api.bootstrap import build_adapters
    from nodo_api.config import Settings

    settings = Settings(
        environment="prod",
        backend="local",
        auth_mode="local",
        local_token=secrets.token_hex(16),
        local_data_dir=data_dir,
        attestation_hmac_key=secrets.token_hex(24),
    )
    return build_adapters(settings)


def peak_mb(pid: int | None = None) -> float | None:
    import psutil

    try:
        info = psutil.Process(pid).memory_info()
    except psutil.Error:
        return None
    return round(getattr(info, "peak_wset", info.rss) / 2**20)


def import_documents(adapters: Any, expected: list[dict[str, Any]]) -> dict[str, Any]:
    service = adapters.source_import
    seconds: Counter = Counter()
    sizes: Counter = Counter()
    captured: list[Any] = []

    def count_text(workspace_id, owner, source_id, fragments) -> None:
        sizes["chars"] += sum(len(f.text) for f in fragments)
        sizes["fragments"] += len(fragments)

    original_screen = service._screen

    def screen(path: Path, name: str) -> None:
        start = time.perf_counter()
        try:
            original_screen(path, name)
        finally:
            seconds["parse"] += time.perf_counter() - start

    service._screen = screen
    service._parser = Timed(service._parser, "parse", seconds, "parse")
    service._index = Timed(service._index, "index", seconds, "index", count_text)
    service._extractor = Timed(service._extractor, "extract_document", seconds, "extract")
    service._graph = Timed(
        service._graph,
        "replace_source_evidence",
        seconds,
        "graph",
        lambda ws, user, ref, extraction, **_: captured.append(extraction),
    )
    totals: Counter = Counter()
    per_language: dict[str, Counter] = {}
    rows = []
    for item in expected:
        path = DOCUMENTS / "files" / item["file"]
        data = path.read_bytes()
        source, session = service.create(
            WORKSPACE,
            OWNER,
            filename=path.name,
            size_bytes=len(data),
            kind=None,
            origin_note=None,
            idempotency_key=None,
        )
        with adapters.source_blobs.open_upload(session.token) as writer:
            writer.write(data)
        captured.clear()
        start = time.perf_counter()
        service.complete(WORKSPACE, OWNER, source.id)
        service.process(WORKSPACE, source.id)
        wall = time.perf_counter() - start
        job = adapters.imports.get_for_processing(WORKSPACE, source.id)
        extraction = captured[-1] if captured else None
        counts = (
            score(extraction.entities, extraction.relations, item)
            if extraction is not None
            else Counter(required=sum(not w.get("optional") for w in item["entities"]))
        )
        totals.update(counts)
        per_language.setdefault(item["language"], Counter()).update(counts)
        rows.append(
            {
                "file": item["file"],
                "state": job.state,
                "error_code": job.error_code,
                "seconds": round(wall, 1),
                **rates(counts),
            }
        )
    pages = sizes["chars"] / PAGE_CHARS
    per_50 = {k: round(v / pages * 50, 1) for k, v in seconds.items()} if pages else {}
    return {
        "documents": rows,
        "chars": sizes["chars"],
        "fragments": sizes["fragments"],
        "pages_equivalent": round(pages, 2),
        "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "seconds_per_50_pages": per_50,
        "quality": rates(totals),
        "quality_by_language": {k: rates(v) for k, v in sorted(per_language.items())},
        "counts": dict(totals),
    }


def extract_meetings(adapters: Any, expected: list[dict[str, Any]]) -> dict[str, Any]:
    from nodo_api.domain.graph import EntityType, validate_extraction
    from nodo_api.domain.models import Locale, TranscriptSegment
    from nodo_api.services.ingestion import with_named_speakers

    fixtures = json.loads(
        (ROOT / "golden_set" / "fixtures" / "meetings.json").read_text(encoding="utf-8")
    )
    meetings = {m["id"]: m for m in fixtures["meetings"]}
    totals: Counter = Counter()
    per_language: dict[str, Counter] = {}
    chars, spent = 0, 0.0
    for item in expected:
        meeting = meetings[item["meeting_id"]]
        locale = Locale(meeting["spoken_language"])
        speakers: dict[str, str] = {}
        segments = []
        for raw in meeting["segments"]:
            if raw.get("tombstoned"):
                continue  # erased text is not extracted
            speaker = speakers.setdefault(raw["speaker"], f"s{len(speakers) + 1}")
            segments.append(
                TranscriptSegment(
                    raw["id"],
                    meeting["id"],
                    speaker,
                    raw["start_ms"],
                    raw["end_ms"],
                    raw["text"],
                    1.0,
                    raw["version"],
                    locale,
                )
            )
            chars += len(raw["text"])
        segments_t = tuple(segments)
        names = {label: name for name, label in speakers.items()}
        start = time.perf_counter()
        raw_extraction = adapters.extractor.extract(segments_t, locale, (), names)
        spent += time.perf_counter() - start
        extraction = with_named_speakers(raw_extraction, segments_t, names)
        clean = validate_extraction(
            extraction, frozenset(s.id for s in segments_t), dict[str, EntityType]()
        )
        counts = score(clean.entities, clean.relations, item)
        totals.update(counts)
        per_language.setdefault(item["language"], Counter()).update(counts)
    pages = chars / PAGE_CHARS
    return {
        "chars": chars,
        "extract_seconds": round(spent, 1),
        "extract_seconds_per_50_pages": round(spent / pages * 50, 1) if pages else None,
        "quality": rates(totals),
        "quality_by_language": {k: rates(v) for k, v in sorted(per_language.items())},
        "counts": dict(totals),
    }


def run_pass(device: str, documents: list, meetings: list | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nodo-measure-") as folder:
        adapters = build_engine(Path(folder))
        runtime = adapters.runtime
        server = runtime.server
        if server is None or adapters.extraction_version is None:
            raise SystemExit("the language model or llama.cpp server is not installed")
        hidden = os.environ.get("CUDA_VISIBLE_DEVICES")
        if device == "cpu":
            server.gpu_layers = 0
            os.environ["CUDA_VISIBLE_DEVICES"] = ""  # also keeps prompt batches off the GPU
        try:
            start = time.perf_counter()
            runtime.embedder.embed_passages(["warm up"])
            embed_load = time.perf_counter() - start
            start = time.perf_counter()
            server.ensure_started()
            llm_load = time.perf_counter() - start
            result = {
                "device": device,
                "load_seconds": {"embedder": round(embed_load, 1), "llm": round(llm_load, 1)},
                "import": import_documents(adapters, documents),
            }
            if meetings is not None:
                result["meetings"] = extract_meetings(adapters, meetings)
            process = server._process
            result["peak_mb"] = {
                "engine_process": peak_mb(),
                "llm_server": peak_mb(process.pid) if process is not None else None,
            }
            return result
        finally:
            if device == "cpu":
                if hidden is None:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
                else:
                    os.environ["CUDA_VISIBLE_DEVICES"] = hidden
            server.stop()
            runtime.db.close()


def main(argv: list[str]) -> int:
    if os.environ.get("NODO_TEST_LOCAL_MODELS") != "1":
        print("set NODO_TEST_LOCAL_MODELS=1: this script needs the installed local models")
        return 2
    from nodo_api.adapters.local.models import LocalModelPaths

    models = LocalModelPaths.discover()
    gpu = gpu_name()
    devices = argv or (["gpu", "cpu"] if gpu else ["cpu"])
    if "gpu" in devices and gpu is None:
        print("no NVIDIA GPU found: the gpu pass is not run")
        devices = [d for d in devices if d != "gpu"]
    documents = read_jsonl(DOCUMENTS / "expected.jsonl")
    meetings = read_jsonl(DOCUMENTS / "meetings_expected.jsonl")
    report: dict[str, Any] = {
        "machine": machine(),
        "models": model_files(models),
        "missing": models.missing(),
        "page_chars": PAGE_CHARS,
        "passes": [],
    }
    for number, device in enumerate(devices):
        report["passes"].append(run_pass(device, documents, meetings if number == 0 else None))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
