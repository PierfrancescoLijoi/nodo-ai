"""The Azure Speech streaming adapters, against a stand-in for the SDK.

This proves the mapping (offsets in 100 ns ticks, speakers, thread hand-over, token renewal,
stopping synthesis on cancel). It does not prove behaviour against the live service."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar

from nodo_api.voice import azure_speech
from nodo_api.voice.azure_speech import AzureStreamingRecognizer, AzureSynthesizer, speaker_number


class Signal:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def connect(self, handler) -> None:
        self.handlers.append(handler)

    def fire(self, event) -> None:
        for handler in self.handlers:
            handler(event)


class Pending:
    def __init__(self, value: Any = None) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value


class FakeEngine:
    def __init__(self, *_: Any) -> None:
        self.recognizing, self.recognized = Signal(), Signal()
        self.transcribing, self.transcribed = Signal(), Signal()
        self.canceled = Signal()
        self.calls: list[str] = []
        self.authorization_token = "t0"

    def start_continuous_recognition_async(self):
        self.calls.append("start")
        return Pending()

    start_transcribing_async = start_continuous_recognition_async

    def stop_continuous_recognition_async(self):
        self.calls.append("stop")
        return Pending()

    stop_transcribing_async = stop_continuous_recognition_async


class FakeStream:
    def __init__(self, *_: Any) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


class FakeSynth:
    def __init__(self, **_: Any) -> None:
        self.stopped = False

    def start_speaking_text_async(self, text: str):
        return Pending(text)

    def stop_speaking_async(self):
        self.stopped = True


class FakeAudioStream:
    chunks: ClassVar[list[bytes]] = [b"\x01\x00" * 100, b"\x02\x00" * 50]

    def __init__(self, result: Any) -> None:
        self._left = list(self.chunks)

    def read_data(self, buffer: bytes) -> int:
        if not self._left:
            return 0
        chunk = self._left.pop(0)
        FakeAudioStream.last = chunk
        return len(chunk)


def fake_sdk(engine: FakeEngine, synth: FakeSynth | None = None) -> Any:
    config = SimpleNamespace(set_speech_synthesis_output_format=lambda fmt: None)
    return SimpleNamespace(
        SpeechConfig=lambda endpoint: config,
        SpeechRecognizer=lambda cfg, audio: engine,
        transcription=SimpleNamespace(ConversationTranscriber=lambda cfg, audio: engine),
        audio=SimpleNamespace(
            PushAudioInputStream=lambda fmt: FakeStream(),
            AudioStreamFormat=lambda **kw: kw,
            AudioConfig=lambda stream: stream,
        ),
        SpeechSynthesizer=lambda **kw: synth,
        SpeechSynthesisOutputFormat=SimpleNamespace(Raw24Khz16BitMonoPcm="pcm24"),
        AudioDataStream=FakeAudioStream,
    )


def event(text: str, offset_ms: int, duration_ms: int, speaker: str | None = None) -> Any:
    result = SimpleNamespace(
        text=text, offset=offset_ms * 10_000, duration=duration_ms * 10_000, speaker_id=speaker
    )
    return SimpleNamespace(result=result)


def test_recognition_events_cross_threads_and_map_offsets_and_speakers():
    async def scenario():
        engine = FakeEngine()
        seen = []
        recognizer = AzureStreamingRecognizer("https://x", lambda: "token", True, fake_sdk(engine))
        await recognizer.start("it", seen.append)

        await asyncio.to_thread(engine.transcribing.fire, event("ciao a", 1000, 400, "Guest-2"))
        await asyncio.to_thread(
            engine.transcribed.fire, event("ciao a tutti", 1000, 1200, "Guest-2")
        )
        await asyncio.to_thread(engine.transcribed.fire, event("  ", 3000, 100))
        recognizer.push(b"\x00\x00")
        await asyncio.sleep(0.02)
        await recognizer.stop()

        assert [(r.kind, r.text, r.start_ms, r.end_ms, r.speaker) for r in seen] == [
            ("partial", "ciao a", 1000, 1400, 2),
            ("final", "ciao a tutti", 1000, 2200, 2),
        ]
        assert engine.calls == ["start", "stop"] and recognizer._stream is not None

    asyncio.run(scenario())


def test_push_to_talk_uses_the_plain_recognizer_and_the_token_is_renewed(monkeypatch):
    async def scenario():
        engine = FakeEngine()
        tokens = iter(["t1", "t2", "t3"])
        monkeypatch.setattr(azure_speech, "TOKEN_REFRESH_SECONDS", 0.01)
        recognizer = AzureStreamingRecognizer(
            "https://x", lambda: next(tokens), False, fake_sdk(engine)
        )
        seen = []
        await recognizer.start("en", seen.append)
        await asyncio.to_thread(engine.recognized.fire, event("hello", 0, 500))
        await asyncio.sleep(0.05)
        await recognizer.stop()

        assert seen[0].kind == "final" and engine.authorization_token in ("t2", "t3")

    asyncio.run(scenario())


def test_synthesis_streams_chunks_and_stops_the_service_when_cancelled():
    async def scenario():
        synth = FakeSynth()
        service = AzureSynthesizer("https://x", lambda: "token", fake_sdk(FakeEngine(), synth))

        chunks = [chunk async for chunk in service.synthesize("ciao", "it")]
        assert [len(c) for c in chunks] == [200, 100] and synth.stopped

        synth.stopped = False
        generator = service.synthesize("ciao", "it")
        await generator.__anext__()
        await generator.aclose()
        assert synth.stopped

    asyncio.run(scenario())


def test_speaker_numbers():
    assert [speaker_number(s) for s in ("Guest-2", "Guest-10", "Unknown", None)] == [2, 10, 1, 1]
