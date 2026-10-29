"""Drive the real voice gateway with many concurrent sessions and write session_metrics input.

Two replicas of the real application run in this process behind separate ports and share one Redis,
exactly as two Container Apps replicas would. The speech provider is scripted (no network, no
model), so what is measured is the gateway: framing, replay, leases, admission, cancellation. It
says nothing about Azure Speech or OpenAI latency, which add to every number here.

    NODO_LOAD_REDIS=redis://localhost:6390/0 PYTHONPATH=services/api/src \
      python tools/voice-bench/gateway_load.py meeting 120 out/meeting.jsonl
    ... assistant 120 out/assistant.jsonl
    python tools/voice-bench/session_metrics.py out/meeting.jsonl out/meeting.json 100 meeting

`meeting`: every session streams 6 s of audio, loses 500 ms of it in transit, is moved to the
second replica by a rolling drain, resumes, ends and must leave exactly the four phrases in the
meeting transcript. `assistant`: push to talk on the real query service; every other session
interrupts the answer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import websockets

from nodo_api.config import Settings
from nodo_api.main import create_app
from nodo_api.voice.protocol import PROTOCOL_VERSION, decode_audio_out, encode_audio_frame
from nodo_api.voice.settings import VoiceSettings
from nodo_api.voice.speech import ScriptedRecognizer, ToneSynthesizer
from nodo_api.voice.wiring import build_voice

FRAME_MS = 100
PCM = b"\x01\x00" * (FRAME_MS * 16)
PHRASES = [
    "we should deploy on monday",
    "sara owns the benchmark",
    "retries moved to the queue",
    "review is on friday",
]
QUESTION = "What changed in the ingestion pipeline?"
TOKEN = "load-test-token-0123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ATTESTATION = {
    "notice_version": "2026-10-v1",
    "locale": "en",
    "retention_policy_id": "retention-30d",
    "acknowledged": True,
}


def build_replica(redis_url: str, port: int, assistant: bool, shared: Any = None):
    """`shared` is the first replica's container: both replicas must see the same meetings, as two
    containers on one PostgreSQL do (the in-memory repositories are per process)."""
    from nodo_api.voice.redis_store import RedisSessionStore

    settings = Settings(
        environment="test",
        auth_mode="dev",
        seed_demo_data=True,
        dev_token=TOKEN,
        dev_user_id="load-user",
        dev_workspace_id="load-ws",
        attestation_hmac_key="load-test-attestation-key-0123456789",
        max_in_flight_requests=2000,
        max_concurrent_queries=64,
    )
    voice = VoiceSettings(
        enabled=True, ticket_key="load-test-ticket-key-0123456789-abcdef"
    ).model_copy(
        update={
            "max_sessions_per_replica": 1000,
            "max_sessions_total": 5000,
            "max_sessions_per_workspace": 1000,
            "max_sessions_per_user": 1000,
        }
    )
    phrases = [QUESTION] if assistant else PHRASES

    def factory(app_settings: Settings, container: Any):
        return build_voice(
            app_settings,
            voice,
            shared or container,
            store=RedisSessionStore.from_url(redis_url),
            recognizer_factory=lambda _record: ScriptedRecognizer(list(phrases), absolute=True),
            synthesizer=ToneSynthesizer(
                chunk_delay_s=0.04 if assistant else 0.0, chars_per_chunk=3
            ),  # ~real-time playback
        )

    app = create_app(settings, voice_factory=factory)
    if shared is not None:
        app.state.container = shared
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    return app, server


DEBUG = bool(os.environ.get("NODO_LOAD_DEBUG"))


def log(*parts: Any) -> None:
    if DEBUG:
        print(f"{time.monotonic() % 1000:8.2f}", *parts, file=sys.stderr, flush=True)


class Session:
    def __init__(self, name: str) -> None:
        self.name = name
        self.partial_ms: list[float] = []
        self.final_ms: list[float] = []
        self.first_audio_ms: list[float] = []
        self.barge_ms: list[float] = []
        self.reconnect_ms: list[float] = []
        self.expected = self.received = self.recovered = self.duplicate = 0
        self.ok = False

    def row(self) -> dict[str, Any]:
        return {
            "session_id": self.name,
            "expected_frames": self.expected,
            "received_frames": self.received,
            "recovered_frames": self.recovered,
            "duplicate_frames": self.duplicate,
            "partial_latencies_ms": self.partial_ms,
            "final_latencies_ms": self.final_ms,
            "first_audio_latencies_ms": self.first_audio_ms,
            "barge_in_stop_latencies_ms": self.barge_ms,
            "reconnect_latencies_ms": self.reconnect_ms,
            "success": self.ok,
        }


def body(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 300:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}"
            f" retry={response.headers.get('retry-after')} {response.text[:120]}"
        )
    return response.json()


async def open_stream(http: httpx.AsyncClient, base: str, ticket: str, last_ack: int = 0):
    ws = await websockets.connect(base.replace("http", "ws") + "/v1/voice/stream", max_size=None)
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": PROTOCOL_VERSION,
                "ticket": ticket,
                "last_ack": last_ack,
            }
        )
    )
    ready = json.loads(await ws.recv())
    return ws, ready


async def meeting_session(
    index: int,
    bases: list[str],
    drain: asyncio.Event,
    connected: list[int],
    everyone: asyncio.Event,
    count: int,
) -> Session:
    result = Session(f"meeting-{index}")
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as http:
        meeting = body(
            await http.post(
                f"{bases[0]}/v1/meetings",
                headers={"Idempotency-Key": f"load-meeting-{index:06d}"},
                json={"recording_attestation": ATTESTATION},
            )
        )["id"]
        session = body(
            await http.post(
                f"{bases[0]}/v1/voice/sessions",
                json={"mode": "meeting", "locale": "en", "meeting_id": meeting},
            )
        )
        ws, _ = await open_stream(http, bases[0], session["ticket"])
        log(result.name, "connected")
        connected.append(index)
        if len(connected) == count:
            everyone.set()
        total_frames = 60
        sent_at: dict[int, float] = {}
        finals: list[str] = []
        state = {"closed_at": None, "last_ack": 0, "error": False}

        async def listen(socket) -> None:
            try:
                async for message in socket:
                    event = json.loads(message)
                    now = time.monotonic()
                    kind = event["type"]
                    if kind in ("transcript.partial", "transcript.final"):
                        end = event["audio_offset_end_ms"]
                        origin = next((t for off, t in sorted(sent_at.items()) if off >= end), None)
                        if origin is not None:
                            (
                                result.partial_ms
                                if kind == "transcript.partial"
                                else result.final_ms
                            ).append((now - origin) * 1000)
                        if kind == "transcript.final":
                            if event["text"] not in finals:
                                finals.append(event["text"])
                            state["last_ack"] = event["sequence"]
                            await socket.send(
                                json.dumps({"type": "ack", "sequence": event["sequence"]})
                            )
                    elif kind == "error":
                        state["error"] = True
                    elif kind == "session.closed":
                        state["summary"] = event
            except websockets.ConnectionClosed:
                state["closed_at"] = time.monotonic()

        listener = asyncio.create_task(listen(ws))
        frame = 0
        lost = range(25, 30)  # these frames never reach the server
        while frame < total_frames:
            if frame == 30 and state["closed_at"] is None:
                await drain.wait()  # the rolling deploy of replica A
                await asyncio.wait_for(listener, 10)
            if state["closed_at"] is not None:
                break
            sent_at[(frame + 1) * FRAME_MS] = time.monotonic()
            if frame not in lost:
                await ws.send(encode_audio_frame(frame * FRAME_MS, PCM))
            frame += 1
            await asyncio.sleep(FRAME_MS / 1000)
        log(result.name, "drained, reconnecting")
        ticket = body(
            await http.post(f"{bases[1]}/v1/voice/sessions/{session['session_id']}/tickets")
        )["ticket"]
        while True:
            try:
                ws, ready = await open_stream(http, bases[1], ticket, state["last_ack"])
            except websockets.ConnectionClosed:
                ready = {"type": "error"}
            log(result.name, "reconnect attempt", ready)
            if ready["type"] == "session.ready":
                break
            await asyncio.sleep(0.3)
            ticket = body(
                await http.post(f"{bases[1]}/v1/voice/sessions/{session['session_id']}/tickets")
            )["ticket"]
        log(result.name, "resumed", ready)
        result.reconnect_ms.append((time.monotonic() - state["closed_at"]) * 1000)
        state["closed_at"] = None
        listener = asyncio.create_task(listen(ws))
        resend_from = ready["audio_resume_offset_ms"] // FRAME_MS
        result.recovered = max(0, 30 - resend_from)
        for index_ in range(resend_from, total_frames):
            sent_at[(index_ + 1) * FRAME_MS] = time.monotonic()
            await ws.send(encode_audio_frame(index_ * FRAME_MS, PCM))
            await asyncio.sleep(FRAME_MS / 1000)
        log(result.name, "ending")
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(listener, 15)
        log(result.name, "closed")
        summary = state.get("summary", {})
        transcript = (await http.get(f"{bases[1]}/v1/meetings/{meeting}/transcript")).json()
        texts = [item["text"] for item in transcript.get("items", [])]
        result.expected = total_frames
        accepted = summary.get("accepted_audio_ms", 0) // FRAME_MS
        result.duplicate = summary.get("duplicate_audio_ms", 0) // FRAME_MS
        # unique audio delivered = what the first replica had durably acknowledged (the resume
        # offset) plus everything the second one accepted; the stretch resent counts as recovered
        result.received = max(0, resend_from + accepted - result.recovered)
        result.ok = bool(summary.get("persisted")) and texts == PHRASES and not state["error"]
    return result


async def assistant_session(index: int, bases: list[str]) -> Session:
    result = Session(f"assistant-{index}")
    interrupt = index % 2 == 1
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as http:
        session = body(
            await http.post(
                f"{bases[0]}/v1/voice/sessions", json={"mode": "assistant", "locale": "en"}
            )
        )
        ws, _ = await open_stream(http, bases[0], session["ticket"])
        inbox: asyncio.Queue[tuple[float, Any]] = asyncio.Queue()

        async def pump(socket) -> None:
            try:
                async for message in socket:
                    await inbox.put((time.monotonic(), message))
            except websockets.ConnectionClosed:
                pass
            await inbox.put((time.monotonic(), None))

        reader = asyncio.create_task(pump(ws))
        sent_at: dict[int, float] = {}
        await ws.send(json.dumps({"type": "ptt.start", "offset_ms": 0}))
        for frame in range(15):
            sent_at[(frame + 1) * FRAME_MS] = time.monotonic()
            await ws.send(encode_audio_frame(frame * FRAME_MS, PCM))
            await asyncio.sleep(FRAME_MS / 1000)
        ended = time.monotonic()
        await ws.send(json.dumps({"type": "ptt.end"}))
        chunks, barged, finished, audio_after_cancel = 0, None, False, 0
        while True:
            received, message = await inbox.get()
            if message is None:
                break
            if isinstance(message, bytes):
                decode_audio_out(message)
                if finished:
                    audio_after_cancel += 1
                    continue
                if not result.first_audio_ms:
                    result.first_audio_ms.append((received - ended) * 1000)
                chunks += 1
                if interrupt and chunks == 3 and barged is None:
                    barged = time.monotonic()
                    await ws.send(json.dumps({"type": "barge_in"}))
                continue
            event = json.loads(message)
            if event["type"] in ("transcript.partial", "transcript.final"):
                origin = next(
                    (
                        t
                        for off, t in sorted(sent_at.items())
                        if off >= event["audio_offset_end_ms"]
                    ),
                    None,
                )
                if origin is not None:
                    (
                        result.partial_ms
                        if event["type"] == "transcript.partial"
                        else result.final_ms
                    ).append((received - origin) * 1000)
            elif event["type"] == "assistant.cancelled" and barged is not None:
                result.barge_ms.append((received - barged) * 1000)
                finished = True
                await asyncio.sleep(0.25)  # nothing may be played after the stop
                while not inbox.empty():
                    _, late = inbox.get_nowait()
                    audio_after_cancel += isinstance(late, bytes)
                break
            elif event["type"] == "assistant.done":
                finished = not interrupt
                break
        # the network drops and the same replica takes the person back within the grace period
        dropped = time.monotonic()
        await ws.close()
        await reader
        ticket = body(
            await http.post(f"{bases[0]}/v1/voice/sessions/{session['session_id']}/tickets")
        )["ticket"]
        ws, ready = await open_stream(http, bases[0], ticket)
        result.reconnect_ms.append((time.monotonic() - dropped) * 1000)
        resumed = ready.get("resumed") is True  # taken back within the grace period
        await ws.send(json.dumps({"type": "end"}))
        summary: dict[str, Any] = {}
        async for message in ws:
            if not isinstance(message, bytes):
                event = json.loads(message)
                if event["type"] == "session.closed":
                    summary = event
        result.expected = 15
        result.received = summary.get("accepted_audio_ms", 0) // FRAME_MS
        result.duplicate = summary.get("duplicate_audio_ms", 0) // FRAME_MS
        result.ok = (
            finished
            and audio_after_cancel == 0
            and bool(summary)
            and ready["type"] == "session.ready"
            and resumed
        )
    return result


async def main(mode: str, count: int, output: Path, redis_url: str) -> None:
    ports = (8841, 8842)
    first = build_replica(redis_url, ports[0], mode == "assistant")
    second = build_replica(redis_url, ports[1], mode == "assistant", first[0].state.container)
    apps, servers = (first[0], second[0]), (first[1], second[1])
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    while not all(server.started for server in servers):
        await asyncio.sleep(0.05)
    bases = [f"http://127.0.0.1:{port}" for port in ports]
    drain = asyncio.Event()
    started = time.monotonic()
    if mode == "meeting":
        connected: list[int] = []
        everyone = asyncio.Event()
        work = [
            asyncio.create_task(meeting_session(i, bases, drain, connected, everyone, count))
            for i in range(count)
        ]
        await asyncio.wait_for(everyone.wait(), 120)  # every session is streaming
        await asyncio.sleep(3.6)
        await apps[0].state.voice.hub.drain()
        drain.set()
    else:
        work = [asyncio.create_task(assistant_session(i, bases)) for i in range(count)]
    sessions = await asyncio.gather(*work, return_exceptions=True)
    elapsed = time.monotonic() - started
    rows = []
    for item in sessions:
        if isinstance(item, Session):
            rows.append(item.row())
        else:
            print("session crashed:", repr(item), file=sys.stderr)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    print(f"{mode}: {len(rows)}/{count} sessions in {elapsed:.1f}s -> {output}")
    for server in servers:
        server.should_exit = True
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    mode, count, output = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    asyncio.run(
        main(mode, count, output, os.environ.get("NODO_LOAD_REDIS", "redis://localhost:6390/0"))
    )
