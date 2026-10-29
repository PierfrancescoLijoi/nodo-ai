"""Shared helpers of the voice tests: a fake socket, PCM, and a way to wait for events."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from nodo_api.voice.live import Deps
from nodo_api.voice.protocol import BYTES_PER_MS, AudioFrame, decode_audio_out
from nodo_api.voice.settings import VoiceSettings
from nodo_api.voice.speech import ScriptedRecognizer, ToneSynthesizer
from nodo_api.voice.store import MemorySessionStore, SessionRecord

KEY = "voice-test-key-0123456789-0123456789"


def settings(**overrides: Any) -> VoiceSettings:
    base = VoiceSettings(enabled=True, ticket_key=KEY)
    return base.model_copy(update=overrides)  # no re-validation: lets tests shrink the timers


def pcm(milliseconds: int) -> bytes:
    return b"\x01\x00" * (milliseconds * BYTES_PER_MS // 2)


def frame(offset_ms: int, milliseconds: int = 100) -> AudioFrame:
    return AudioFrame(offset_ms, pcm(milliseconds))


def record(mode: str = "meeting", **fields: Any) -> SessionRecord:
    values = {
        "session_id": "s1",
        "workspace_id": "w1",
        "user_id": "u1",
        "mode": mode,
        "locale": "en",
        "meeting_id": "m1" if mode == "meeting" else None,
    }
    return SessionRecord(**(values | fields))


class FakeConnection:
    def __init__(self, capacity: int = 10_000) -> None:
        self.events: list[dict[str, Any]] = []
        self.audio: list[tuple[int, int, bytes]] = []
        self.closed: int | None = None
        self.purged = 0
        self._capacity = capacity

    def send_json(self, data: dict[str, Any]) -> bool:
        if len(self.events) >= self._capacity:
            return False
        self.events.append(data)
        return True

    def send_bytes(self, data: bytes) -> bool:
        self.audio.append(decode_audio_out(data))
        return True

    def purge_audio(self) -> None:
        self.purged += 1

    def close(self, code: int) -> None:
        self.closed = code

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == type_]


class FakeFinalizer:
    def __init__(self) -> None:
        self.calls: list[tuple[SessionRecord, list[dict[str, Any]]]] = []

    async def finalize(self, record: SessionRecord, finals: list[dict[str, Any]]) -> bool:
        self.calls.append((record, finals))
        return True


def deps(
    phrases: list[str] | None = None,
    *,
    store: Any = None,
    replica: str = "r1",
    assistant: Any = None,
    finalizer: Any = None,
    **overrides: Any,
) -> Deps:
    script = phrases or ["we should deploy on monday", "sara owns the benchmark"]
    return Deps(
        store=store or MemorySessionStore(),
        settings=settings(**overrides),
        replica_id=replica,
        recognizer_factory=lambda _record: ScriptedRecognizer(list(script)),
        assistant=assistant,
        finalizer=finalizer,
    )


def tone() -> ToneSynthesizer:
    return ToneSynthesizer()


async def until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)
