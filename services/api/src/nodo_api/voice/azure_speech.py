"""Azure AI Speech streaming recognition and synthesis, on the Speech SDK.

Entra authentication, as for the batch path: a bearer token for the resource's custom subdomain,
renewed before it expires so a long meeting does not end at the token's lifetime. The SDK calls
back on its own threads; every result is handed to the event loop with call_soon_threadsafe.
The SDK is imported lazily and can be injected, so the mapping below is tested without Azure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from nodo_api.voice.protocol import INPUT_SAMPLE_RATE
from nodo_api.voice.speech import Recognition, RecognitionSink

logger = logging.getLogger("nodo_api.voice")

TICKS_PER_MS = 10_000  # the SDK reports offsets in 100 ns units
TOKEN_REFRESH_SECONDS = 8 * 60  # Entra tokens last at least 10 minutes
SYNTH_CHUNK_BYTES = 9600  # 200 ms at 24 kHz
LOCALES = {"en": "en-US", "pl": "pl-PL", "it": "it-IT", "da": "da-DK"}
VOICES = {
    "en": "en-GB-SoniaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "it": "it-IT-ElsaNeural",
    "da": "da-DK-ChristelNeural",
}


def _sdk() -> Any:
    import azure.cognitiveservices.speech as speechsdk

    return speechsdk


def _config(sdk: Any, endpoint: str, token: str) -> Any:
    config = sdk.SpeechConfig(endpoint=endpoint)
    config.authorization_token = token
    return config


def speaker_number(speaker_id: str | None) -> int:
    """'Guest-2' -> 2; the service's unknown speaker maps to 1."""
    digits = "".join(ch for ch in (speaker_id or "") if ch.isdigit())
    return int(digits) if digits else 1


class AzureStreamingRecognizer:
    def __init__(
        self,
        endpoint: str,
        token_provider: Callable[[], str],
        diarize: bool,
        sdk: Any | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._token = token_provider
        self._diarize = diarize
        self._sdk = sdk or _sdk()
        self._stream: Any = None
        self._engine: Any = None
        self._refresh: asyncio.Task[None] | None = None

    async def start(self, locale: str, sink: RecognitionSink, base_ms: int = 0) -> None:
        sdk, loop = self._sdk, asyncio.get_running_loop()
        token = await asyncio.to_thread(self._token)
        config = _config(sdk, self._endpoint, token)
        config.speech_recognition_language = LOCALES.get(locale, "en-US")
        self._stream = sdk.audio.PushAudioInputStream(
            sdk.audio.AudioStreamFormat(
                samples_per_second=INPUT_SAMPLE_RATE, bits_per_sample=16, channels=1
            )
        )
        audio = sdk.audio.AudioConfig(stream=self._stream)
        if self._diarize:
            self._engine = sdk.transcription.ConversationTranscriber(config, audio)
            interim, final = self._engine.transcribing, self._engine.transcribed
        else:
            self._engine = sdk.SpeechRecognizer(config, audio)
            interim, final = self._engine.recognizing, self._engine.recognized
        interim.connect(lambda event: self._deliver(loop, sink, "partial", event))
        final.connect(lambda event: self._deliver(loop, sink, "final", event))
        self._engine.canceled.connect(
            lambda event: logger.warning("voice_speech_canceled reason=%s", event.reason)
        )
        begin = (
            self._engine.start_transcribing_async
            if self._diarize
            else self._engine.start_continuous_recognition_async
        )
        await asyncio.to_thread(lambda: begin().get())
        self._refresh = asyncio.create_task(self._renew_token())

    def _deliver(
        self, loop: asyncio.AbstractEventLoop, sink: RecognitionSink, kind: str, event: Any
    ) -> None:
        result = event.result
        text = (result.text or "").strip()
        if not text:
            return
        start = int(result.offset // TICKS_PER_MS)
        recognition = Recognition(
            kind,  # type: ignore[arg-type]
            text,
            start,
            start + int(result.duration // TICKS_PER_MS),
            speaker=speaker_number(getattr(result, "speaker_id", None)),
        )
        loop.call_soon_threadsafe(sink, recognition)

    async def _renew_token(self) -> None:
        while True:
            await asyncio.sleep(TOKEN_REFRESH_SECONDS)
            try:
                self._engine.authorization_token = await asyncio.to_thread(self._token)
            except Exception:
                logger.warning("voice_token_refresh_failed")

    def push(self, pcm: bytes) -> None:
        if self._stream is not None:
            self._stream.write(pcm)

    async def stop(self) -> None:
        if self._refresh is not None:
            self._refresh.cancel()
        if self._engine is None:
            return
        self._stream.close()
        stop = (
            self._engine.stop_transcribing_async
            if self._diarize
            else self._engine.stop_continuous_recognition_async
        )
        await asyncio.to_thread(lambda: stop().get())
        self._engine = None


class AzureSynthesizer:
    def __init__(
        self, endpoint: str, token_provider: Callable[[], str], sdk: Any | None = None
    ) -> None:
        self._endpoint = endpoint
        self._token = token_provider
        self._sdk = sdk or _sdk()

    async def synthesize(self, text: str, locale: str) -> AsyncIterator[bytes]:
        sdk = self._sdk
        token = await asyncio.to_thread(self._token)
        config = _config(sdk, self._endpoint, token)
        config.speech_synthesis_voice_name = VOICES.get(locale, VOICES["en"])
        config.set_speech_synthesis_output_format(
            sdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
        )
        synthesizer = sdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        try:
            result = await asyncio.to_thread(
                lambda: synthesizer.start_speaking_text_async(text).get()
            )
            stream = sdk.AudioDataStream(result)
            while True:
                buffer = bytes(SYNTH_CHUNK_BYTES)
                filled = await asyncio.to_thread(stream.read_data, buffer)
                if not filled:
                    return
                yield bytes(buffer[:filled])
        finally:  # a barge-in cancels the generator: stop the service from synthesizing on
            with contextlib.suppress(Exception):
                synthesizer.stop_speaking_async()
