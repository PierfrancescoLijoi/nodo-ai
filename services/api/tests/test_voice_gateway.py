"""The voice gateway through the real application: REST for sessions and tickets, a real
WebSocket, the real query service on the demo data, and the real meeting pipeline at the end."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nodo_api.config import Settings
from nodo_api.main import create_app
from nodo_api.voice.protocol import PROTOCOL_VERSION, decode_audio_out, encode_audio_frame
from nodo_api.voice.speech import ScriptedRecognizer, ToneSynthesizer
from nodo_api.voice.store import MemorySessionStore
from nodo_api.voice.wiring import build_voice
from tests.voice_support import pcm
from tests.voice_support import settings as voice_settings

QUESTION = "What changed in the ingestion pipeline?"
OTHER = {"Authorization": "Bearer another-person-token-0123456789"}


def make_client(settings: Settings, phrases: list[str], **voice: Any) -> TestClient:
    def factory(app_settings: Settings, container: Any):
        runtime = build_voice(
            app_settings,
            voice_settings(**voice),
            container,
            store=MemorySessionStore(),
            recognizer_factory=lambda _record: ScriptedRecognizer(list(phrases)),
            synthesizer=ToneSynthesizer(),
        )
        container.auth.add_dev_token("another-person-token-0123456789", "user-b", "workspace-a")
        return runtime

    return TestClient(create_app(settings, voice_factory=factory))


@pytest.fixture
def voice_client(settings):
    with make_client(settings, [QUESTION]) as client:
        yield client


def open_session(client, headers, **body) -> dict[str, Any]:
    response = client.post("/v1/voice/sessions", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def hello(ws, ticket: str, last_ack: int = 0) -> dict[str, Any]:
    ws.send_text(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": PROTOCOL_VERSION,
                "ticket": ticket,
                "last_ack": last_ack,
            }
        )
    )
    return receive(ws)


def receive(ws) -> Any:
    message = ws.receive()
    if message["type"] == "websocket.disconnect":
        return {"type": "disconnected", "code": message.get("code")}
    if message.get("bytes") is not None:
        return decode_audio_out(message["bytes"])
    return json.loads(message["text"])


def collect(ws, until_type: str, limit: int = 200) -> list[Any]:
    seen = []
    for _ in range(limit):
        item = receive(ws)
        seen.append(item)
        if isinstance(item, dict) and item["type"] in (until_type, "disconnected"):
            return seen
    raise AssertionError(f"no {until_type} after {limit} messages")


def speak(ws, start_ms: int, total_ms: int, step: int = 500) -> None:
    for offset in range(start_ms, start_ms + total_ms, step):
        ws.send_bytes(encode_audio_frame(offset, pcm(step)))


def test_a_spoken_question_is_answered_from_the_meetings_with_citations_and_audio(
    voice_client, auth_headers
):
    session = open_session(voice_client, auth_headers, mode="assistant", locale="en")

    with voice_client.websocket_connect(session["stream_path"]) as ws:
        ready = hello(ws, session["ticket"])
        assert (
            ready["type"] == "session.ready"
            and ready["mode"] == "assistant"
            and not ready["resumed"]
        )
        ws.send_text('{"type":"ptt.start","offset_ms":0}')
        speak(ws, 0, 1500)
        ws.send_text('{"type":"ptt.end"}')
        events = collect(ws, "assistant.done")

    typed = [e for e in events if isinstance(e, dict)]
    assert [
        e["type"] for e in typed if e["type"] in ("transcript.final", "assistant.thinking")
    ] == [
        "transcript.final",
        "assistant.thinking",
    ]
    final = next(e for e in typed if e["type"] == "transcript.final")
    assert final["text"] == QUESTION
    answer = next(e for e in typed if e["type"] == "assistant.answer")
    assert answer["answerability"] == "supported" and answer["citations"]
    assert any(isinstance(e, tuple) for e in events), "no audio reached the client"
    written = voice_client.post(
        "/v1/query", headers=auth_headers, json={"question": QUESTION, "ui_locale": "en"}
    ).json()
    assert answer["citations"][0]["segment_id"] in {c["segment_id"] for c in written["citations"]}


def test_a_ticket_works_once(voice_client, auth_headers):
    session = open_session(voice_client, auth_headers, mode="assistant", locale="en")

    with voice_client.websocket_connect(session["stream_path"]) as ws:
        assert hello(ws, session["ticket"])["type"] == "session.ready"
    with voice_client.websocket_connect(session["stream_path"]) as ws:
        refusal = hello(ws, session["ticket"])

    assert refusal["type"] == "error" and refusal["code"] == "ticket.used"


def test_a_ticket_is_useless_to_another_person_and_without_a_hello(voice_client, auth_headers):
    session = open_session(voice_client, auth_headers, mode="assistant", locale="en")

    stolen = voice_client.post(f"/v1/voice/sessions/{session['session_id']}/tickets", headers=OTHER)
    anonymous = voice_client.post(f"/v1/voice/sessions/{session['session_id']}/tickets")
    with voice_client.websocket_connect(session["stream_path"]) as ws:
        ws.send_text('{"type":"ping"}')
        refusal = receive(ws)
    with voice_client.websocket_connect(session["stream_path"]) as ws:
        garbage = hello(ws, "x" * 40)

    assert stolen.status_code == 404 and anonymous.status_code == 401
    assert refusal["code"] == "hello.required" and garbage["code"] == "ticket.invalid"


def test_opening_a_session_needs_the_person_and_validates_the_request(voice_client, auth_headers):
    body = {"mode": "assistant", "locale": "en"}

    assert voice_client.post("/v1/voice/sessions", json=body).status_code == 401
    assert (
        voice_client.post(
            "/v1/voice/sessions", headers=auth_headers, json={"mode": "x", "locale": "en"}
        ).status_code
        == 422
    )
    assert (
        voice_client.post(
            "/v1/voice/sessions", headers=auth_headers, json=body | {"meeting_id": "m"}
        ).status_code
        == 422
    )
    assert (
        voice_client.post(
            "/v1/voice/sessions", headers=auth_headers, json={"mode": "meeting", "locale": "en"}
        ).status_code
        == 422
    )
    assert (
        voice_client.post(
            "/v1/voice/sessions",
            headers=OTHER,
            json={"mode": "meeting", "locale": "en", "meeting_id": "seed-m1"},
        ).status_code
        == 404
    )  # somebody else's meeting


def test_sessions_beyond_the_workspace_limit_get_a_retry_after(settings):
    with make_client(settings, [QUESTION], max_sessions_per_workspace=1) as client:
        headers = {"Authorization": "Bearer test-dev-token-0123456789"}
        open_session(client, headers, mode="assistant", locale="en")

        refused = client.post(
            "/v1/voice/sessions", headers=headers, json={"mode": "assistant", "locale": "en"}
        )

    assert refused.status_code == 429 and refused.headers["Retry-After"]


def test_a_live_meeting_reconnects_and_ends_as_a_normal_meeting_ready_for_the_pipeline(
    settings, attestation
):
    phrases = [
        "we should deploy on monday",
        "sara owns the benchmark",
        "retries moved to the queue",
    ]
    with make_client(settings, phrases) as client:
        headers = {"Authorization": "Bearer test-dev-token-0123456789"}
        meeting = client.post(
            "/v1/meetings",
            headers={**headers, "Idempotency-Key": "live-meeting-0001"},
            json={"recording_attestation": attestation},
        ).json()["id"]
        session = open_session(client, headers, mode="meeting", locale="en", meeting_id=meeting)

        with client.websocket_connect(session["stream_path"]) as ws:
            assert hello(ws, session["ticket"])["type"] == "session.ready"
            speak(ws, 0, 3000)
            first = collect_finals(ws, 2)
        assert [f["sequence"] for f in first] == [1, 2]

        again = client.post(
            f"/v1/voice/sessions/{session['session_id']}/tickets", headers=headers
        ).json()
        with client.websocket_connect(session["stream_path"]) as ws:
            ready = hello(ws, again["ticket"], last_ack=1)
            assert (
                ready["resumed"]
                and ready["replayed"] == 1
                and ready["audio_resume_offset_ms"] == 3000
            )
            replayed = receive(ws)
            assert replayed["sequence"] == 2 and replayed["text"] == "sara owns the benchmark"
            speak(ws, 3000, 1500)
            ws.send_text('{"type":"end"}')
            closing = collect(ws, "session.closed")[-2:]
        assert closing[-1]["persisted"] is True

        state = client.get(f"/v1/meetings/{meeting}", headers=headers).json()
        transcript = client.get(f"/v1/meetings/{meeting}/transcript", headers=headers).json()
        assert state["state"] == "transcribing"
        assert [s["text"] for s in transcript["items"]] == phrases
        assert [s["speaker_id"] for s in transcript["items"]] == ["S1", "S1", "S1"]


def collect_finals(ws, count: int) -> list[dict[str, Any]]:
    finals: list[dict[str, Any]] = []
    while len(finals) < count:
        item = receive(ws)
        if isinstance(item, dict) and item["type"] == "transcript.final":
            finals.append(item)
    return finals


def test_a_second_live_session_for_a_meeting_that_has_already_ended_is_refused(
    settings, attestation
):
    with make_client(settings, ["one phrase here"]) as client:
        headers = {"Authorization": "Bearer test-dev-token-0123456789"}
        meeting = client.post(
            "/v1/meetings",
            headers={**headers, "Idempotency-Key": "live-meeting-0002"},
            json={"recording_attestation": attestation},
        ).json()["id"]
        client.post(f"/v1/meetings/{meeting}/upload-sessions", headers=headers)
        client.post(f"/v1/meetings/{meeting}/complete", headers=headers)

        refused = client.post(
            "/v1/voice/sessions",
            headers=headers,
            json={"mode": "meeting", "locale": "en", "meeting_id": meeting},
        )

    assert refused.status_code == 409


def test_a_bad_frame_closes_the_socket_with_a_protocol_error(voice_client, auth_headers):
    session = open_session(voice_client, auth_headers, mode="assistant", locale="en")

    with voice_client.websocket_connect(session["stream_path"]) as ws:
        hello(ws, session["ticket"])
        ws.send_bytes(b"\x00\x00\x00\x00abc")
        error = receive(ws)

    assert error["type"] == "error" and error["code"] == "frame.too_short"


def test_the_gateway_is_absent_unless_enabled(client):
    assert client.post("/v1/voice/sessions", json={}).status_code == 404


def test_a_session_that_could_not_be_stored_gives_its_slot_back(settings):
    with make_client(settings, [QUESTION], max_sessions_per_user=1) as client:
        headers = {"Authorization": "Bearer test-dev-token-0123456789"}
        store = client.app.state.voice.deps.store
        original = store.create

        async def broken(*_args):
            raise ConnectionError("redis gone")

        store.create = broken
        with pytest.raises(ConnectionError):
            client.post(
                "/v1/voice/sessions", headers=headers, json={"mode": "assistant", "locale": "en"}
            )
        store.create = original

        assert open_session(client, headers, mode="assistant", locale="en")["session_id"]


def test_sockets_that_have_not_said_hello_are_limited(settings):
    with make_client(settings, [QUESTION], max_pending_hello=1) as client:
        with (
            client.websocket_connect("/v1/voice/stream"),  # waiting for its hello
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect("/v1/voice/stream"),
        ):
            pass

        assert refused.value.code == 1013
        with client.websocket_connect("/v1/voice/stream"):  # the first one gave its place back
            pass


def test_a_ping_is_answered_and_a_dead_session_ends_the_pump(voice_client, auth_headers):
    session = open_session(voice_client, auth_headers, mode="assistant", locale="en")

    with voice_client.websocket_connect(session["stream_path"]) as ws:
        hello(ws, session["ticket"])
        ws.send_text('{"type":"ping"}')
        assert receive(ws)["type"] == "pong"
        ws.send_text('{"type":"end"}')
        closing = collect(ws, "session.closed")

    assert closing[-1]["type"] == "session.closed"
