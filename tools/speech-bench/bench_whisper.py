"""Measure local Whisper (faster-whisper) on the corpus: speed, memory, language id, word errors.

    python tools/speech-bench/bench_whisper.py <model> <device> <compute_type> [corpus_dir] [threads] [results.jsonl]

Prints one JSON line per recording. Run each configuration in its own process so memory numbers are
clean (see run_matrix.py).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

try:  # CUDA 12 runtime libraries ship inside the torch wheel; make them visible to CTranslate2
    import os

    import torch

    os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))
except (ImportError, OSError, AttributeError):
    pass

from faster_whisper import WhisperModel

NUMBER_WORDS = {
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteenth",
    "fifteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "hundred",
    "thousand",
    "million",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "uno",
    "due",
    "tre",
    "quattro",
    "cinque",
    "sei",
    "sette",
    "otto",
    "nove",
    "dieci",
    "undici",
    "dodici",
    "tredici",
    "quattordici",
    "quindici",
    "venti",
    "trenta",
    "quaranta",
    "cinquanta",
    "cento",
    "mille",
    "milione",
    "un",
    "una",
}


def words(text: str, drop_numbers: bool) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold().replace("'", " ")
    tokens = re.findall(r"[^\W_]+", text)
    if drop_numbers:
        tokens = [t for t in tokens if not t.isdigit() and t not in NUMBER_WORDS]
    return tokens


def wer(reference: list[str], hypothesis: list[str]) -> float:
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, start=1):
        row = [i]
        for j, hyp in enumerate(hypothesis, start=1):
            row.append(
                min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ref != hyp))
            )
        previous = row
    return previous[-1] / max(len(reference), 1)


def load_pcm(path: Path) -> np.ndarray:
    # decoded here, not by faster-whisper's PyAV, so the benchmark does not depend on the PyAV version
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def vram_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return int(out.splitlines()[0]) if out else 0


def main() -> None:
    model_name, device, compute = sys.argv[1:4]
    corpus = Path(sys.argv[4] if len(sys.argv) > 4 else "tmp/speech-bench")
    threads = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    output_path = Path(sys.argv[6]) if len(sys.argv) > 6 else None
    vram_before = vram_mb()
    started = time.perf_counter()
    model = WhisperModel(
        model_name, device=device, compute_type=compute, cpu_threads=threads
    )
    load_s = time.perf_counter() - started
    vram_loaded = vram_mb() - vram_before

    results = []
    for wav in sorted(corpus.glob("*.wav")):
        reference = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        started = time.perf_counter()
        segments, info = model.transcribe(
            load_pcm(wav),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        segments = list(segments)  # decoding happens lazily while iterating
        elapsed = time.perf_counter() - started
        text = " ".join(s.text for s in segments)
        truth = " ".join(t["text"] for t in reference["turns"])
        result = {
            "model": model_name,
            "device": device,
            "compute": compute,
            "file": wav.stem,
            "locale": reference.get("locale", info.language),
            "scenario": reference.get("scenario", "clean"),
            "audio_s": round(info.duration, 1),
            "wall_s": round(elapsed, 3),
            "load_s": round(load_s, 2),
            "decode_s": round(elapsed, 2),
            "rtf": round(elapsed / info.duration, 3),
            "lang": info.language,
            "lang_p": round(info.language_probability, 3),
            "wer": round(wer(words(truth, False), words(text, False)), 3),
            "wer_no_numbers": round(wer(words(truth, True), words(text, True)), 3),
            "vram_mb": max(vram_mb() - vram_before, vram_loaded),
            "segments": len(segments),
            "reference": truth,
            "hypothesis": text,
        }
        results.append(result)
        print(json.dumps(result), flush=True)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(
                json.dumps(result, ensure_ascii=False) + "\n" for result in results
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
