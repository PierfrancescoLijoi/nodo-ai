"""Ports of the streaming speech path, and the implementations that need no cloud.

`StreamingRecognizer` takes PCM as it arrives and reports partial and final phrases through a
sink that is always called on the event loop thread (an adapter whose SDK calls back on its own
threads hands over with call_soon_threadsafe). Offsets in a recognition are relative to the first
sample the recognizer received; the session adds the base offset.
"""

from __future__ import annotations

import asyncio
import tempfile
import wave
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from nodo_api.domain.models import SpokenPhrase
from nodo_api.voice.protocol import BYTES_PER_MS, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

MAX_BUFFERED_SECONDS = 120  # a push-to-talk question is a sentence, not a meeting


@dataclass(frozen=True, slots=True)
class Recognition:
    kind: Literal["partial", "final"]
    text: str
    start_ms: int
    end_ms: int
    speaker: int = 1
    confidence: float = 0.0
    locale: str = ""


RecognitionSink = Callable[[Recognition], None]


class StreamingRecognizer(Protocol):
    async def start(self, locale: str, sink: RecognitionSink, base_ms: int = 0) -> None:
        """`base_ms` is where the first sample sits in the session; results stay relative to it."""
        ...

    def push(self, pcm: bytes) -> None:
        """Never blocks: a recognizer that falls behind buffers or drops; the socket never waits."""
        ...

    async def stop(self) -> None:
        """Flush: every final the audio so far produces has reached the sink when this returns."""
        ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, locale: str) -> AsyncIterator[bytes]:
        """PCM16 mono at OUTPUT_SAMPLE_RATE, in chunks, as soon as the first one is ready."""
        ...


class BufferedRecognizer:
    """Push-to-talk on an engine that only transcribes finished audio (the local Whisper).

    The question is buffered, written to a temporary WAV file when the key is released and handed
    to `transcribe`, which is the same port the batch path uses. There are no partial results.
    """

    def __init__(
        self,
        transcribe: Callable[[str], tuple[SpokenPhrase, ...]],
        max_seconds: int = MAX_BUFFERED_SECONDS,
    ) -> None:
        self._transcribe = transcribe
        self._limit = max_seconds * INPUT_SAMPLE_RATE * 2
        self._chunks: list[bytes] = []
        self._size = 0
        self._sink: RecognitionSink | None = None
        self._locale = ""

    async def start(self, locale: str, sink: RecognitionSink, base_ms: int = 0) -> None:
        self._locale, self._sink = locale, sink

    def push(self, pcm: bytes) -> None:
        if self._size + len(pcm) <= self._limit:
            self._chunks.append(pcm)
            self._size += len(pcm)

    async def stop(self) -> None:
        if not self._chunks or self._sink is None:
            return
        audio = b"".join(self._chunks)
        self._chunks.clear()
        phrases = await asyncio.to_thread(self._run, audio)
        for phrase in phrases:
            self._sink(
                Recognition(
                    "final",
                    phrase.text,
                    phrase.start_ms,
                    phrase.end_ms,
                    phrase.speaker,
                    phrase.confidence,
                    phrase.locale or self._locale,
                )
            )

    def _run(self, audio: bytes) -> tuple[SpokenPhrase, ...]:
        with tempfile.TemporaryDirectory(prefix="nodo-voice-") as directory:
            path = Path(directory) / "question.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(INPUT_SAMPLE_RATE)
                out.writeframes(audio)
            return self._transcribe(str(path))


class ScriptedRecognizer:
    """Deterministic recognizer for tests and the load generator: it 'hears' a fixed script.

    Phrase i covers [i * phrase_ms, (i + 1) * phrase_ms). Audio yields a partial with the words so
    far and a final once the phrase is covered. With `absolute` the script is laid along the whole
    session, so a recognizer started at 3000 ms (a resume on another replica) continues the script
    where the first one stopped; otherwise every recognizer starts at the top. `delay_s` stands in
    for provider latency.
    """

    def __init__(
        self,
        phrases: list[str],
        phrase_ms: int = 1500,
        delay_s: float = 0.0,
        absolute: bool = False,
    ) -> None:
        self._phrases = phrases
        self._phrase_ms = phrase_ms
        self._delay = delay_s
        self._absolute = absolute
        self._origin = 0
        self._position = 0
        self._emitted = 0
        self._sink: RecognitionSink | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.started = False

    async def start(self, locale: str, sink: RecognitionSink, base_ms: int = 0) -> None:
        self._sink, self._loop, self.started = sink, asyncio.get_running_loop(), True
        self._origin = base_ms if self._absolute else 0
        self._position = self._origin
        self._emitted = self._origin // self._phrase_ms

    def push(self, pcm: bytes) -> None:
        self._position += len(pcm) // BYTES_PER_MS
        covered = min(self._position // self._phrase_ms, len(self._phrases))
        while self._emitted < covered:
            self._emit("final", self._emitted)
            self._emitted += 1
        into = self._position - self._emitted * self._phrase_ms
        if self._emitted < len(self._phrases) and into > 0:
            words = self._phrases[self._emitted].split()
            seen = max(1, len(words) * into // self._phrase_ms)
            self._emit("partial", self._emitted, " ".join(words[:seen]), self._position)

    async def stop(self) -> None:
        if self._position % self._phrase_ms and self._emitted < len(self._phrases):
            self._emit("final", self._emitted, end_ms=self._position)
            self._emitted += 1

    def _emit(
        self,
        kind: Literal["partial", "final"],
        index: int,
        text: str | None = None,
        end_ms: int | None = None,
    ) -> None:
        assert self._sink is not None and self._loop is not None
        end = end_ms if end_ms is not None else (index + 1) * self._phrase_ms
        recognition = Recognition(
            kind,
            text if text is not None else self._phrases[index],
            max(index * self._phrase_ms - self._origin, 0),
            end - self._origin,
            speaker=1,
            confidence=0.95,
        )
        if self._delay:
            self._loop.call_later(self._delay, self._sink, recognition)
        else:
            self._sink(recognition)


class ToneSynthesizer:
    """Stand-in voice: a silent chunk per ~40 ms of text, so playback timing and cancellation can
    be exercised without a speech service."""

    def __init__(self, chunk_delay_s: float = 0.0, chars_per_chunk: int = 12) -> None:
        self._delay = chunk_delay_s
        self._chars = chars_per_chunk

    async def synthesize(self, text: str, locale: str) -> AsyncIterator[bytes]:
        silence = b"\x00\x00" * (OUTPUT_SAMPLE_RATE // 25)
        for _ in range(0, max(len(text), 1), self._chars):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield silence
