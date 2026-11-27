"""Offline Apache-2.0 Flock Speech adapter for the local voice gateway.

Flock ships its Junco voice with the Python package and returns 24 kHz float PCM.  Importing it is
deliberately lazy: installations that only record/transcribe need not load a neural TTS model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from nodo_api.voice.protocol import OUTPUT_SAMPLE_RATE


class FlockSpeechSynthesizer:
    def __init__(self) -> None:
        self._voice = None

    async def synthesize(self, text: str, locale: str) -> AsyncIterator[bytes]:
        if not locale.startswith("en"):
            raise ValueError("The bundled Junco voice currently supports English only")
        pcm = await asyncio.to_thread(self._render, text)
        # 40 ms chunks let the browser start playback while a longer answer continues.
        chunk_bytes = OUTPUT_SAMPLE_RATE * 2 // 25
        for start in range(0, len(pcm), chunk_bytes):
            yield pcm[start : start + chunk_bytes]

    def _render(self, text: str) -> bytes:
        try:
            import numpy as np
            from flockspeech import Junco
        except ImportError as error:
            raise RuntimeError(
                "Install nodo-api[local-models] to enable local speech output"
            ) from error
        if self._voice is None:
            self._voice = Junco()
        audio = np.asarray(self._voice(text), dtype=np.float32)
        return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
