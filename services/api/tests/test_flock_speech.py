from __future__ import annotations

import asyncio
import sys
import types

import numpy as np
import pytest

from nodo_api.voice.flock_speech import FlockSpeechSynthesizer


async def _convert():
    chunks = [chunk async for chunk in FlockSpeechSynthesizer().synthesize("Hello", "en")]

    assert b"".join(chunks) == np.array([-32767, 0, 32767], dtype="<i2").tobytes()


async def _refuse():
    with pytest.raises(ValueError, match="English"):
        _ = [chunk async for chunk in FlockSpeechSynthesizer().synthesize("Ciao", "it")]


def test_flock_adapter_converts_local_float_audio_to_24khz_pcm(monkeypatch):
    package = types.ModuleType("flockspeech")
    package.Junco = lambda: lambda _: np.array([-1.0, 0.0, 1.0], dtype=np.float32)
    monkeypatch.setitem(sys.modules, "flockspeech", package)
    asyncio.run(_convert())


def test_flock_adapter_refuses_unsupported_locales():
    asyncio.run(_refuse())
