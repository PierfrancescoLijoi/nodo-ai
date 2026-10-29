"""The golden-set runner on the in-memory backend: wiring, isolation and deleted text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("nodo_api")

import run_queries  # noqa: E402

ROOT = Path(__file__).resolve().parent


def _predictions(tmp_path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    cases = {
        c["id"]: c
        for c in map(json.loads, (ROOT / "golden_set/cases.jsonl").read_text("utf-8").splitlines())
    }
    meetings = json.loads((ROOT / "golden_set/fixtures/meetings.json").read_text("utf-8"))["meetings"]
    _, adapters = run_queries.build_adapters("memory", tmp_path)
    counts = run_queries.load_fixtures(adapters, meetings)
    assert counts == {"segments_indexed": 28, "segments_left_out_as_deleted": 2}
    from nodo_api.config import Settings
    from nodo_api.services.query import EvidenceValidator, QueryService

    settings = Settings(
        environment="test", auth_mode="dev", attestation_hmac_key="x" * 40, dev_token="y" * 24
    )
    service = QueryService(
        adapters.meetings,
        adapters.retriever,
        adapters.graph,
        adapters.llm,
        EvidenceValidator(adapters.transcripts),
        settings.graph_node_limit,
    )
    rows = run_queries.run_pass(service, list(cases.values()), "en")
    return cases, {r["id"]: r for r in rows}


def test_no_case_returns_text_of_another_workspace_or_of_deleted_segments(tmp_path) -> None:
    cases, rows = _predictions(tmp_path)
    assert set(rows) == set(cases)
    for case_id, case in cases.items():
        if case["category"] in ("acl_negative", "deleted_data"):
            leaked = set(rows[case_id]["citation_segment_ids"]) & set(case["forbidden_segment_ids"])
            assert not leaked, (case_id, leaked)


def test_questions_are_picked_in_the_requested_language() -> None:
    question = {"en": "What?", "it": "Cosa?"}
    assert run_queries.pick_question(question, "it") == "Cosa?"
    assert run_queries.pick_question(question, "da") == "What?"  # falls back to English
    assert run_queries.pick_question("plain", "pl") == "plain"


def test_a_repeat_pass_is_compared_case_by_case() -> None:
    first = [
        {"answerability": "supported", "citation_segment_ids": ["a"], "answer": "x", "latency_ms": 900},
        {"answerability": "unanswerable", "citation_segment_ids": [], "answer": "", "latency_ms": 700},
    ]
    second = [{**first[0], "latency_ms": 5}, {**first[1], "answer": "changed", "latency_ms": 650}]
    summary = run_queries.compare_passes(first, second)
    assert summary["identical_on_repeat"] == 1 and summary["cases"] == 2
