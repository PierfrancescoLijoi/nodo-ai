"""The API under stress: too many requests, too large bodies, failing dependencies."""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from nodo_api.middleware import EdgeProtection
from nodo_api.resilience import DependencyUnavailable, Overloaded


async def _call(app, path="/v1/x", method="POST", headers=(), body=b"", chunks=None):
    sent = []
    scope = {"type": "http", "path": path, "method": method, "headers": list(headers)}
    parts = chunks if chunks is not None else [body]
    queue = [
        {"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
        for i, p in enumerate(parts)
    ]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def _ok_app(scope, receive, send):
    while (await receive())["type"] == "http.request" and False:  # pragma: no cover
        pass
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _reading_app(scope, receive, send):
    more = True
    while more:
        message = await receive()
        more = message.get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"read"})


def _status(messages):
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def _headers(messages):
    start = next(m for m in messages if m["type"] == "http.response.start")
    return {k.decode(): v.decode() for k, v in start["headers"]}


def test_responses_carry_security_headers() -> None:
    app = EdgeProtection(_ok_app, max_in_flight=10)
    headers = _headers(asyncio.run(_call(app)))
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "max-age" in headers["strict-transport-security"]


def test_a_declared_oversized_body_is_refused_before_it_is_read() -> None:
    app = EdgeProtection(_reading_app, max_in_flight=10, default_body_limit=100)
    messages = asyncio.run(_call(app, headers=[(b"content-length", b"101")], body=b"x" * 101))
    assert _status(messages) == 413


def test_a_chunked_body_is_cut_off_when_it_passes_the_limit() -> None:
    app = EdgeProtection(_reading_app, max_in_flight=10, default_body_limit=100)
    messages = asyncio.run(_call(app, chunks=[b"x" * 60, b"x" * 60]))
    assert _status(messages) == 413


def test_limits_are_per_route_family() -> None:
    app = EdgeProtection(
        _reading_app, max_in_flight=10, default_body_limit=100, body_limits=[("/v1/sync/", 10_000)]
    )
    assert _status(asyncio.run(_call(app, path="/v1/sync/m", body=b"x" * 5000))) == 200
    assert _status(asyncio.run(_call(app, path="/v1/other", body=b"x" * 5000))) == 413


def test_past_the_in_flight_limit_requests_are_shed_but_health_probes_are_not() -> None:
    gate = asyncio.Event()

    async def slow_app(scope, receive, send):
        await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def scenario():
        app = EdgeProtection(slow_app, max_in_flight=2)
        busy = [asyncio.create_task(_call(app)) for _ in range(2)]
        await asyncio.sleep(0.05)
        shed = await _call(app)
        probe_task = asyncio.create_task(_call(app, path="/healthz", method="GET"))
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*busy, probe_task)
        return app, shed, results

    app, shed, results = asyncio.run(scenario())
    assert _status(shed) == 503 and _headers(shed)["retry-after"] == "2"
    assert all(_status(r) == 200 for r in results)  # the busy ones and the health probe
    assert app.in_flight == 0  # nothing leaked


# --- through the real application ----------------------------------------------------------------


def test_a_failing_model_degrades_the_answer_to_its_sources(app, client, auth_headers) -> None:
    class ModelDown:
        def answer(self, *args, **kwargs):
            raise DependencyUnavailable("openai-chat")

    app.state.container.query_service._llm = ModelDown()
    body = client.post(
        "/v1/query",
        headers=auth_headers,
        json={"question": "What changed in the ingestion pipeline?", "ui_locale": "en"},
    ).json()
    assert body["degraded"] is True and body["answer"] == ""
    assert body["answerability"] == "partial" and body["citations"]


def test_a_failing_search_is_a_503_with_retry_after_not_a_500(app, client, auth_headers) -> None:
    class SearchDown:
        def search(self, *args, **kwargs):
            raise DependencyUnavailable("search", retry_after=7)

    app.state.container.query_service._retriever = SearchDown()
    reply = client.post(
        "/v1/query", headers=auth_headers, json={"question": "anything at all", "ui_locale": "en"}
    )
    assert reply.status_code == 503 and reply.headers["retry-after"] == "7"
    assert reply.json()["error"]["code"] == "dependency_unavailable"


def test_an_unwrapped_timeout_from_a_dependency_is_a_503_too(app, auth_headers) -> None:
    class ReadTimeout(Exception):
        pass

    def broken(*args, **kwargs):
        raise ReadTimeout

    app.state.container.meetings.list_owned = broken
    with TestClient(app, raise_server_exceptions=False) as quiet:
        reply = quiet.get("/v1/meetings", headers=auth_headers)
    assert reply.status_code == 503 and reply.headers["retry-after"] == "5"


def test_a_burst_of_questions_is_limited_per_replica(app, client, auth_headers) -> None:
    gate, entered = threading.Event(), threading.Event()
    service = app.state.container.query_service
    original = service.query

    def slow(*args, **kwargs):
        entered.set()
        gate.wait(3)
        return original(*args, **kwargs)

    service.query = slow
    app.state.container.query_gate = type(app.state.container.query_gate)(
        "query", 1, wait_seconds=0
    )
    results = []

    def ask():
        results.append(
            client.post(
                "/v1/query", headers=auth_headers, json={"question": "ingestion", "ui_locale": "en"}
            )
        )

    first = threading.Thread(target=ask)
    first.start()
    entered.wait(2)
    second = client.post(
        "/v1/query", headers=auth_headers, json={"question": "ingestion", "ui_locale": "en"}
    )
    gate.set()
    first.join()
    assert second.status_code == 429 and second.headers["retry-after"] == "2"
    assert results[0].status_code == 200


def test_readiness_reports_the_failing_check_without_details(app, client) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}

    def database() -> None:
        raise RuntimeError("password=hunter2 host=internal")

    app.state.container.readiness = (("database", database),)
    reply = client.get("/readyz")
    assert reply.status_code == 503
    assert reply.json() == {"status": "not_ready", "failed": ["database"]}
    assert "hunter2" not in reply.text
    assert client.get("/healthz").status_code == 200  # liveness never depends on dependencies


def test_overloaded_and_unavailable_carry_their_status_codes() -> None:
    assert Overloaded("x").status_code == 429 and DependencyUnavailable("x").status_code == 503
    with pytest.raises(Overloaded):
        raise Overloaded("query")
