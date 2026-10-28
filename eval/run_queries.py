"""Run the golden set through the real query path and write predictions for score_results.py.

    services/api/.venv/Scripts/python eval/run_queries.py memory results.jsonl [locale]
    services/api/.venv/Scripts/python eval/run_queries.py local results.jsonl [locale]

`memory` uses the mock retriever and model: it proves the wiring, workspace and owner isolation and
the handling of deleted text, not answer quality. `local` uses what the desktop app uses (SQLite,
the E5 embedder, the local language model), so latency and quality are the product's. Each fixture
meeting is stored and indexed as a finished meeting; segments marked tombstoned are left out, as the
real erasure leaves them out. The graph starts empty: this measures search plus answer.

A second pass asks every question again. It reports how many were served by the answer cache, whether
they returned exactly what the first pass did, and the latency saved (ADR-026).
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodo_api.config import Settings
from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.services.query import EvidenceValidator, QueryRequest, QueryService

ROOT = Path(__file__).resolve().parent
OWNERS = {"workspace-north": "user-north-01", "workspace-south": "user-south-01"}


def build_adapters(backend: str, data_dir: Path):
    from nodo_api.bootstrap import build_adapters as build

    if backend == "local":
        settings = Settings(
            environment="prod",
            backend="local",
            auth_mode="local",
            local_token=secrets.token_hex(16),
            local_data_dir=data_dir,
            attestation_hmac_key=secrets.token_hex(24),
        )
    else:
        settings = Settings(
            environment="test",
            auth_mode="dev",
            attestation_hmac_key="eval-attestation-key-0123456789",
            dev_token="eval-dev-token-0123456789",
        )
    return settings, build(settings)


def load_fixtures(adapters: Any, meetings: list[dict[str, Any]]) -> dict[str, int]:
    kept = dropped = 0
    now = datetime.now(UTC)
    for item in meetings:
        workspace, owner = item["workspace_id"], OWNERS[item["workspace_id"]]
        locale = Locale(item["spoken_language"])
        meeting = Meeting(
            id=item["id"],
            workspace_id=workspace,
            owner_id=owner,
            created_at=now,
            state=MeetingState.REVIEW,
            attestation=RecordingAttestation(
                owner,
                item["id"],
                "2026-10-v1",
                now,
                "retention-30d",
                locale,
                "eval",
                "eval",
            ),
            project=item.get("project_id"),
            title=item.get("title"),
        )
        adapters.meetings.add(workspace, meeting, f"eval-{item['id']}", "eval")
        segments = tuple(
            TranscriptSegment(
                s["id"],
                item["id"],
                s["speaker"],
                s["start_ms"],
                s["end_ms"],
                s["text"],
                1.0,
                s["version"],
                locale,
                item.get("project_id"),
            )
            for s in item["segments"]
            if not s.get("tombstoned")
        )
        dropped += len(item["segments"]) - len(segments)
        kept += len(segments)
        adapters.transcripts.add_version(workspace, item["id"], segments)
        adapters.retriever.index(workspace, owner, segments)
    return {"segments_indexed": kept, "segments_left_out_as_deleted": dropped}


class CountingLlm:
    """Counts the calls that reach the language model, to tell cache hits from fresh answers."""

    def __init__(self, inner: Any) -> None:
        self._inner, self.calls = inner, 0

    def answer(self, *args: Any) -> Any:
        self.calls += 1
        return self._inner.answer(*args)


def pick_question(question: Any, locale: str) -> str:
    return (
        question
        if isinstance(question, str)
        else question.get(locale) or question["en"]
    )


def run_pass(
    service: QueryService, cases: list[dict[str, Any]], locale: str
) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        request = QueryRequest(pick_question(case["question"], locale), Locale(locale))
        started = time.perf_counter()
        result = service.query(case["workspace_id"], case["caller_user_id"], request)
        rows.append(
            {
                "id": case["id"],
                "answerability": result.answerability.value,
                "citation_segment_ids": [c.segment_id for c in result.citations],
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "answer": result.answer,
                "degraded": result.degraded,
            }
        )
    return rows


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def compare_passes(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> dict[str, Any]:
    same = sum(
        1
        for a, b in zip(first, second, strict=True)
        if (a["answerability"], a["citation_segment_ids"], a["answer"])
        == (b["answerability"], b["citation_segment_ids"], b["answer"])
    )
    fast = sorted(b["latency_ms"] for b in second)
    slow = sorted(a["latency_ms"] for a in first)
    return {
        "cases": len(first),
        "identical_on_repeat": same,
        "median_ms_first": slow[len(slow) // 2],
        "median_ms_repeat": fast[len(fast) // 2],
    }


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("memory", "local"):
        print(__doc__)
        return 2
    backend, output = sys.argv[1], Path(sys.argv[2])
    locale = sys.argv[3] if len(sys.argv) > 3 else "en"
    cases = [
        json.loads(line)
        for line in (ROOT / "golden_set/cases.jsonl").read_text("utf-8").splitlines()
        if line
    ]
    meetings = json.loads(
        (ROOT / "golden_set/fixtures/meetings.json").read_text("utf-8")
    )["meetings"]

    with tempfile.TemporaryDirectory(prefix="nodo-eval-") as folder:
        settings, adapters = build_adapters(backend, Path(folder))
        print(json.dumps(load_fixtures(adapters, meetings)))
        llm = CountingLlm(adapters.llm)
        service = QueryService(
            adapters.meetings,
            adapters.retriever,
            adapters.graph,
            llm,
            EvidenceValidator(adapters.transcripts),
            settings.graph_node_limit,
            adapters.entity_indexer,
            adapters.answers,
        )
        started = time.perf_counter()
        warmup = run_pass(
            service, cases[:1], locale
        )  # the model starts here, not in the timings
        print(
            f"warm-up {time.perf_counter() - started:.1f} s ({warmup[0]['latency_ms']} ms for the call)"
        )
        service._answers and service._answers.invalidate(
            cases[0]["workspace_id"], cases[0]["caller_user_id"]
        )
        llm.calls = 0
        first = run_pass(service, cases, locale)
        calls_first, llm.calls = llm.calls, 0
        second = run_pass(service, cases, locale)
        calls_repeat = llm.calls
        write(output, first)
        write(output.with_name(output.stem + "-repeat.jsonl"), second)
        print(
            json.dumps(
                {
                    **compare_passes(first, second),
                    "model_calls_first": calls_first,
                    "model_calls_repeat": calls_repeat,
                }
            )
        )
        if adapters.runtime is not None:
            adapters.runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
