"""Benchmark the exact local speaker-separation path used by the product.

    python tools/speech-bench/bench_diarization.py [corpus_dir] [models_dir] [ffmpeg]

Reports end-to-end RTF (decode + VAD + ERes2Net diarization + timestamp restoration), the share of
audio retained by VAD, speaker accuracy and speaker-count error.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from bench_whisper import load_pcm
from faster_whisper.vad import VadOptions, get_speech_timestamps
from nodo_api.adapters.local.diarize_worker import separate
from score import speaker_accuracy, speaker_count_error

VAD_OPTIONS = VadOptions(
    threshold=0.35,
    min_speech_duration_ms=100,
    min_silence_duration_ms=500,
    speech_pad_ms=250,
)


def main() -> None:
    corpus = Path(sys.argv[1] if len(sys.argv) > 1 else "tmp/speech-bench")
    default_models = Path(os.environ.get("LOCALAPPDATA", ".")) / "NODO" / "models" / "diarization"
    models = Path(sys.argv[2] if len(sys.argv) > 2 else default_models)
    ffmpeg = sys.argv[3] if len(sys.argv) > 3 else shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required")
    for wav in sorted(corpus.glob("*.wav")):
        reference = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        truth = [(t["speaker"], t["start"], t["end"]) for t in reference["turns"]]
        audio = load_pcm(wav)
        started = time.perf_counter()
        result = separate(str(wav), ffmpeg, models)
        elapsed = time.perf_counter() - started
        hyp = [(f"S{speaker}", start, end) for start, end, speaker in result]
        chunks = get_speech_timestamps(audio, VAD_OPTIONS, sampling_rate=16_000)
        active_samples = sum(chunk["end"] - chunk["start"] for chunk in chunks)
        print(
            json.dumps(
                {
                    "file": wav.stem,
                    "pipeline": "production-vad-eres2net",
                    "audio_seconds": round(len(audio) / 16_000, 3),
                    "active_audio_ratio": round(active_samples / max(len(audio), 1), 3),
                    "elapsed_seconds": round(elapsed, 3),
                    "rtf": round(elapsed / (len(audio) / 16_000), 3),
                    "speaker_accuracy": round(speaker_accuracy(truth, hyp), 3),
                    "speaker_count_error": speaker_count_error(truth, hyp),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
