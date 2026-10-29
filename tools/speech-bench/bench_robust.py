"""Robustness and throughput of one Whisper configuration on the corpus.

    python tools/speech-bench/bench_robust.py <model> <device> <compute_type> [corpus_dir]

Cases per recording: clean, white noise at 20/10/5 dB SNR, babble (the other language's meeting mixed
in at 0 dB, i.e. a second conversation of equal loudness), and faster-whisper's batched pipeline at
several batch sizes. Also a long recording (the corpus repeated up to ~10 minutes) for steady-state
speed and memory.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

try:
    import torch

    os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))
except (ImportError, OSError, AttributeError):
    pass

from bench_whisper import load_pcm, vram_mb, wer, words  # noqa: E402
from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402

RNG = np.random.default_rng(7)


def add_noise(audio: np.ndarray, snr_db: float) -> np.ndarray:
    power = float(np.mean(audio**2))
    noise = RNG.normal(0, np.sqrt(power / 10 ** (snr_db / 10)), audio.shape).astype(np.float32)
    return np.clip(audio + noise, -1, 1)


def babble(audio: np.ndarray, other: np.ndarray) -> np.ndarray:
    other = np.resize(other, audio.shape)
    gain = np.sqrt(np.mean(audio**2) / max(np.mean(other**2), 1e-9))
    return np.clip(audio + other * gain, -1, 1)


def score(model, audio: np.ndarray, truth: str, language: str, batched: int = 0) -> dict:
    started = time.perf_counter()
    kwargs = {"beam_size": 5, "language": language}
    if batched:
        segments, _ = model.transcribe(audio, batch_size=batched, **kwargs)
    else:
        segments, _ = model.transcribe(audio, vad_filter=True, condition_on_previous_text=False, **kwargs)
    text = " ".join(s.text for s in segments)
    elapsed = time.perf_counter() - started
    return {
        "wer_no_numbers": round(wer(words(truth, True), words(text, True)), 3),
        "rtf": round(elapsed / (len(audio) / 16000), 3),
    }


def main() -> None:
    model_name, device, compute = sys.argv[1:4]
    corpus = Path(sys.argv[4] if len(sys.argv) > 4 else "tmp/speech-bench")
    model = WhisperModel(model_name, device=device, compute_type=compute)
    batched = BatchedInferencePipeline(model=model)
    clips = {}
    for wav in sorted(corpus.glob("*.wav")):
        ref = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        clips[wav.stem] = (load_pcm(wav), " ".join(t["text"] for t in ref["turns"]))
    langs = list(clips)

    for lang in langs:
        audio, truth = clips[lang]
        other = clips[next(x for x in langs if x != lang)][0]
        cases = {
            "clean": audio,
            "snr20": add_noise(audio, 20),
            "snr10": add_noise(audio, 10),
            "snr5": add_noise(audio, 5),
            "babble0dB": babble(audio, other),
        }
        for name, signal in cases.items():
            print(json.dumps({"lang": lang, "case": name, **score(model, signal, truth, lang)}), flush=True)
        for size in (4, 8, 16):
            result = score(batched, audio, truth, lang, size)
            print(json.dumps({"lang": lang, "case": f"batched{size}", **result}), flush=True)

    audio, truth = clips[langs[0]]
    long_audio = np.tile(audio, 6)
    started = time.perf_counter()
    result = score(batched, long_audio, truth * 6, langs[0], 8)
    print(
        json.dumps(
            {
                "case": "long_batched8",
                "minutes": round(len(long_audio) / 16000 / 60, 1),
                **result,
                "wall_s": round(time.perf_counter() - started, 1),
                "vram_mb": vram_mb(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
