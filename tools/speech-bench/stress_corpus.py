"""Create deterministic meeting-audio stress cases from a clean reference corpus.

Usage:
    python tools/speech-bench/stress_corpus.py tmp/speech-bench tmp/speech-stress

Every input ``.wav`` needs the adjacent reference JSON produced by ``make_corpus.py``. The output
contains one WAV and one reference JSON per condition plus ``manifest.jsonl``. Transformations are
deterministic, so regressions can be compared across commits and Azure/local providers.
"""

from __future__ import annotations

import json
import math
import sys
import wave
from pathlib import Path

import numpy as np


def _rms(signal: np.ndarray) -> float:
    return math.sqrt(float(np.mean(np.square(signal, dtype=np.float64))))


def add_white_noise(signal: np.ndarray, snr_db: float, *, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, signal.shape).astype(np.float32)
    noise *= _rms(signal) / max(_rms(noise) * 10 ** (snr_db / 20), 1e-12)
    return np.clip(signal + noise, -1.0, 1.0).astype(np.float32)


def measured_snr_db(reference: np.ndarray, degraded: np.ndarray) -> float:
    error = degraded - reference
    return 20 * math.log10(max(_rms(reference), 1e-12) / max(_rms(error), 1e-12))


def apply_echo(
    signal: np.ndarray, *, rate: int, delay_ms: int = 120, decay: float = 0.35
) -> np.ndarray:
    delay = round(rate * delay_ms / 1000)
    result = signal.copy()
    result[delay:] += signal[:-delay] * decay
    return np.clip(result, -1.0, 1.0).astype(np.float32)


def apply_clipping(signal: np.ndarray, *, threshold: float = 0.12) -> np.ndarray:
    return np.clip(signal, -threshold, threshold).astype(np.float32)


def apply_dropouts(
    signal: np.ndarray,
    rate: int,
    *,
    seed: int,
    events_per_minute: int = 12,
    duration_ms: int = 120,
) -> np.ndarray:
    result = signal.copy()
    rng = np.random.default_rng(seed)
    event_count = max(1, math.ceil(len(signal) / rate / 60 * events_per_minute))
    width = max(1, round(rate * duration_ms / 1000))
    for start in rng.integers(0, max(len(signal) - width, 1), size=event_count):
        result[start : start + width] = 0
    return result.astype(np.float32)


def telephone_band(signal: np.ndarray, rate: int) -> np.ndarray:
    spectrum = np.fft.rfft(signal)
    frequencies = np.fft.rfftfreq(len(signal), 1 / rate)
    spectrum[(frequencies < 300) | (frequencies > 3400)] = 0
    return np.fft.irfft(spectrum, n=len(signal)).astype(np.float32)


def mix_babble(signal: np.ndarray, interfering: np.ndarray) -> np.ndarray:
    resized = np.resize(interfering, signal.shape)
    resized *= _rms(signal) / max(_rms(resized), 1e-12)
    return np.clip(signal + resized, -1.0, 1.0).astype(np.float32)


def build_cases(
    signal: np.ndarray, *, rate: int, interfering: np.ndarray, seed: int = 20261023
) -> dict[str, np.ndarray]:
    return {
        "clean": signal.astype(np.float32, copy=True),
        "snr20": add_white_noise(signal, 20, seed=seed + 20),
        "snr10": add_white_noise(signal, 10, seed=seed + 10),
        "snr5": add_white_noise(signal, 5, seed=seed + 5),
        "echo": apply_echo(signal, rate=rate),
        "clipping": apply_clipping(signal),
        "dropouts": apply_dropouts(signal, rate, seed=seed),
        "telephone": telephone_band(signal, rate),
        "babble": mix_babble(signal, interfering),
    }


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16-bit mono PCM")
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768, rate


def write_wav(path: Path, signal: np.ndarray, rate: int) -> None:
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def create_corpus(source: Path, destination: Path, *, seed: int = 20261023) -> int:
    inputs = sorted(source.glob("*.wav"))
    if not inputs:
        raise ValueError(f"no WAV files found in {source}")
    destination.mkdir(parents=True, exist_ok=True)
    loaded = {path.stem: read_wav(path) for path in inputs}
    rows = []
    for index, wav in enumerate(inputs):
        reference_path = wav.with_suffix(".json")
        if not reference_path.exists():
            raise ValueError(f"missing reference file: {reference_path}")
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        signal, rate = loaded[wav.stem]
        other_name = inputs[(index + 1) % len(inputs)].stem
        interfering, other_rate = loaded[other_name]
        if other_rate != rate:
            raise ValueError("all corpus WAV files must use the same sample rate")
        if len(inputs) == 1:
            interfering = np.roll(signal, rate * 2)
        for scenario, degraded in build_cases(
            signal, rate=rate, interfering=interfering, seed=seed + index * 100
        ).items():
            case_id = f"{wav.stem}-{scenario}"
            write_wav(destination / f"{case_id}.wav", degraded, rate)
            case_reference = {
                **reference,
                "case_id": case_id,
                "source": wav.name,
                "scenario": scenario,
                "seed": seed + index * 100,
            }
            (destination / f"{case_id}.json").write_text(
                json.dumps(case_reference, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rows.append(
                {
                    "case_id": case_id,
                    "wav": f"{case_id}.wav",
                    "reference": f"{case_id}.json",
                    "locale": reference["locale"],
                    "scenario": scenario,
                    "duration_seconds": round(len(degraded) / rate, 3),
                }
            )
    (destination / "manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return len(rows)


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: stress_corpus.py SOURCE_DIR DESTINATION_DIR [SEED]", file=sys.stderr
        )
        return 2
    count = create_corpus(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        seed=int(sys.argv[3]) if len(sys.argv) == 4 else 20261023,
    )
    print(f"created {count} deterministic stress cases in {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
