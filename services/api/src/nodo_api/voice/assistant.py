"""One spoken turn: the question goes through the same ACL-aware retrieval and validated answer
path as the written one, then the answer is cut into sentences and spoken one at a time.

Nothing is invented here: the text and the citations come from the existing query service, so a
spoken answer is as grounded as a written one and carries the same citations to the screen.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nodo_api.domain.models import Answerability
from nodo_api.voice.chunker import speech_chunks
from nodo_api.voice.speech import SpeechSynthesizer

logger = logging.getLogger("nodo_api.voice")

NOT_FOUND = {
    "en": "I did not find that in your meetings.",
    "pl": "Nie znalazłem tego w Twoich spotkaniach.",
    "it": "Non l'ho trovato nei tuoi meeting.",
    "da": "Det fandt jeg ikke i dine møder.",
}
UNAVAILABLE = {
    "en": "I can not answer right now. Try again in a moment.",
    "pl": "Nie mogę teraz odpowiedzieć. Spróbuj za chwilę.",
    "it": "Non riesco a rispondere ora. Riprova tra poco.",
    "da": "Jeg kan ikke svare lige nu. Prøv igen om lidt.",
}

Ask = Callable[[str, str, str, str], Awaitable[Any]]  # workspace, user, question, locale
Emit = Callable[..., Any]
SendAudio = Callable[[int, int, bytes], Awaitable[None]]


def _citation(citation: Any) -> dict[str, Any]:
    return {
        "meeting_id": citation.meeting_id,
        "segment_id": citation.segment_id,
        "start_ms": citation.start_ms,
        "end_ms": citation.end_ms,
        "quote": citation.quote,
        "speaker_id": citation.speaker_id,
    }


class Assistant:
    def __init__(self, ask: Ask, synthesizer: SpeechSynthesizer | None) -> None:
        self._ask = ask
        self._synthesizer = synthesizer

    async def respond(
        self,
        workspace_id: str,
        user_id: str,
        locale: str,
        question: str,
        turn: int,
        emit: Emit,
        send_audio: SendAudio,
    ) -> None:
        emit("assistant.thinking", turn=turn)
        try:
            result = await self._ask(workspace_id, user_id, question, locale)
        except Exception as error:  # the recording and the transcript go on without the assistant
            logger.warning("voice_answer_failed type=%s", type(error).__name__)
            emit("assistant.error", turn=turn, code="assistant_unavailable", retryable=True)
            await self._speak(
                UNAVAILABLE.get(locale, UNAVAILABLE["en"]), locale, turn, 0, emit, send_audio
            )
            emit("assistant.done", turn=turn)
            return
        emit(
            "assistant.answer",
            turn=turn,
            answerability=str(result.answerability),
            degraded=bool(result.degraded),
            citations=[_citation(c) for c in result.citations],
        )
        text = result.answer
        if not text.strip() and result.answerability is Answerability.UNANSWERABLE:
            text = NOT_FOUND.get(locale, NOT_FOUND["en"])
        index = 0
        for piece in speech_chunks(text):
            index = await self._speak(piece, locale, turn, index, emit, send_audio)
        emit("assistant.done", turn=turn)

    async def _speak(
        self, text: str, locale: str, turn: int, index: int, emit: Emit, send_audio: SendAudio
    ) -> int:
        emit("assistant.text", turn=turn, text=text)
        if self._synthesizer is None:
            return index
        started = time.monotonic()
        async for pcm in self._synthesizer.synthesize(text, locale):
            if index == 0:
                emit("assistant.audio_start", turn=turn, sample_rate=24_000, encoding="pcm_s16le")
            await send_audio(turn, index, pcm)
            index += 1
        logger.debug("voice_sentence_spoken ms=%d", int((time.monotonic() - started) * 1000))
        return index
