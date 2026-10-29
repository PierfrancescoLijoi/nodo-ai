"""The smoke test run against a small imitation of the API, to prove the script itself."""

from __future__ import annotations

import json

import httpx
import pytest
from smoke import Smoke, main, silence_wav


class FakeApi:
    """Just enough of the API: a meeting that ends as `end_state`, optionally over a blob link."""

    def __init__(self, end_state: str = "failed", blob: bool = False, breaks: str | None = None):
        self.end_state, self.blob, self.breaks = end_state, blob, breaks
        self.polls = 0
        self.deleted = False
        self.calls: list[tuple[str, str]] = []
        self.blob_puts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if self.breaks == path:
            return httpx.Response(500)
        if request.url.host.endswith("blob.core.windows.net"):
            self.blob_puts.append(str(request.url))
            return httpx.Response(201)
        if path in ("/healthz", "/readyz"):
            return httpx.Response(200)
        if "authorization" not in request.headers and not path.startswith("/local/"):
            return httpx.Response(401)
        if (method, path) == ("GET", "/v1/meetings"):
            return httpx.Response(200, json={"items": []})
        if (method, path) == ("POST", "/v1/meetings"):
            assert request.headers["idempotency-key"].startswith("smoke-")
            return httpx.Response(201, json={"id": "m1"})
        if path == "/v1/meetings/m1/upload-sessions":
            url = (
                "https://stnodo.blob.core.windows.net/audio/w/m1/audio?sig=x"
                if self.blob
                else "http://testserver/local/uploads/tok"
            )
            return httpx.Response(200, json={"upload_url": url})
        if path == "/local/uploads/tok":
            return httpx.Response(204)
        if path == "/v1/meetings/m1/complete":
            return httpx.Response(200, json={"state": "transcribing"})
        if (method, path) == ("GET", "/v1/meetings/m1"):
            if self.deleted:
                return httpx.Response(404)
            self.polls += 1
            return httpx.Response(200, json={"state": self.end_state if self.polls > 2 else "transcribing"})
        if path == "/v1/meetings/m1/transcript":
            return httpx.Response(200, json={"total": 3})
        if path == "/v1/query":
            return httpx.Response(200, json={"answerability": "supported"})
        if path == "/v1/graph/subgraph":
            return httpx.Response(200, json={"nodes": [{}, {}]})
        if (method, path) == ("POST", "/v1/export"):
            assert json.loads(request.content) == {"meeting_id": "m1"}
            return httpx.Response(202, json={"job_id": "x1", "status": "queued"})
        if (method, path) == ("GET", "/v1/export/x1"):
            return httpx.Response(
                200, json={"job_id": "x1", "status": "ready", "download_path": "/v1/export/x1/download"}
            )
        if path == "/v1/export/x1/download":
            return httpx.Response(200, json={"meetings": [{"id": "m1"}]})
        if (method, path) == ("DELETE", "/v1/meetings/m1"):
            self.deleted = True
            return httpx.Response(204)
        return httpx.Response(404)


def run(api: FakeApi, **kw) -> Smoke:
    transport = httpx.MockTransport(api)
    client = httpx.Client(base_url="http://testserver", transport=transport)
    smoke = Smoke(
        client, "token", sleep=lambda s: None, poll=0, blob_client=httpx.Client(transport=transport), **kw
    )
    smoke.run()
    return smoke


def names(smoke: Smoke) -> dict[str, bool]:
    return {s.name: s.ok for s in smoke.steps}


def test_silence_ends_as_failed_and_that_is_the_pass_condition() -> None:
    smoke = run(FakeApi("failed"))
    assert all(names(smoke).values())
    assert list(names(smoke)) == ["health", "auth", "record", "upload", "process", "export", "delete"]


def test_real_speech_must_reach_review_and_is_then_read_questioned_and_graphed() -> None:
    smoke = run(FakeApi("review"), audio=silence_wav(1))
    assert all(names(smoke).values())
    assert list(names(smoke)) == [
        "health", "auth", "record", "upload", "process", "transcript", "question", "graph", "export", "delete",
    ]  # fmt: skip


def test_the_wrong_outcome_fails_the_run_but_still_cleans_up() -> None:
    api = FakeApi("review")  # silence should have failed
    smoke = run(api)
    assert names(smoke)["process"] is False
    assert "expected 'failed'" in next(s for s in smoke.steps if s.name == "process").detail
    assert api.deleted


def test_blob_links_are_used_with_the_blob_header_and_no_bearer_token() -> None:
    api = FakeApi("failed", blob=True)
    smoke = run(api)
    assert all(names(smoke).values()) and len(api.blob_puts) == 1


def test_a_broken_endpoint_stops_the_path_at_that_step() -> None:
    smoke = run(FakeApi(breaks="/readyz"))
    assert names(smoke) == {"health": False}


def test_keep_leaves_the_meeting_in_place() -> None:
    api = FakeApi("failed")
    run(api, keep=True)
    assert not api.deleted


def test_a_missing_token_is_a_usage_error_not_a_crash(monkeypatch, capsys) -> None:
    monkeypatch.delenv("NODO_SMOKE_TOKEN", raising=False)
    assert main(["https://api.example.com"]) == 2
    assert "NODO_SMOKE_TOKEN" in capsys.readouterr().err


def test_the_generated_audio_is_a_valid_silent_wav() -> None:
    import io
    import wave

    with wave.open(io.BytesIO(silence_wav(2))) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16_000, 32_000)


@pytest.mark.parametrize("state", ["ready", "review"])
def test_ready_counts_as_review(state) -> None:
    assert all(names(run(FakeApi(state), audio=b"x")).values())
