from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from nodo_api.voice.flock_speech import FlockSpeechSynthesizer


@pytest.mark.asyncio
async def test_flock_adapter_converts_local_float_audio_to_24khz_pcm(monkeypatch):
    package = types.ModuleType("flockspeech")
    package.Junco = lambda: lambda _: np.array([-1.0, 0.0, 1.0], dtype=np.float32)
    monkeypatch.setitem(sys.modules, "flockspeech", package)

    chunks = [chunk async for chunk in FlockSpeechSynthesizer().synthesize("Hello", "en")]

    assert b"".join(chunks) == np.array([-32767, 0, 32767], dtype="<i2").tobytes()


@pytest.mark.asyncio
async def test_flock_adapter_refuses_unsupported_locales():
    with pytest.raises(ValueError, match="English"):
        _ = [chunk async for chunk in FlockSpeechSynthesizer().synthesize("Ciao", "it")]
