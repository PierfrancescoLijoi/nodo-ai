"""Time each step of local speech to text on one file: decode, language, words, speakers."""

from __future__ import annotations

import sys
import time

from nodo_api.adapters.local.models import GpuArbiter, LocalModelPaths
from nodo_api.adapters.local.speech import LocalSpeechToText, assign_speakers, build_phrases, decode_audio

CANDIDATES = ("en-GB", "pl-PL", "it-IT", "da-DK")


def lap(label: str, since: float) -> float:
    now = time.perf_counter()
    print(f"[{now - since:6.1f} s] {label}")
    return now


def main() -> None:
    path = sys.argv[1]
    models = LocalModelPaths.discover()
    speech = LocalSpeechToText(models, GpuArbiter())
    t = time.perf_counter()
    proc = speech._start_separation(path)  # runs in parallel with everything below
    audio = decode_audio(path, models.ffmpeg)
    t = lap(f"decode ({len(audio) / 16000:.0f} s of audio)", t)
    speech._load_whisper()
    t = lap("load whisper", t)
    model = speech._load_whisper()
    _, _, probs = model.detect_language(audio, language_detection_segments=4)
    t = lap("detect language", t)
    language = speech.pick_language(probs, CANDIDATES)
    if speech._batched is not None:
        segments, _ = speech._batched.transcribe(audio, batch_size=8, language=language, beam_size=5, word_timestamps=True)
    else:
        segments, _ = model.transcribe(audio, language=language, beam_size=5, word_timestamps=True)
    words = [w for s in segments for w in (s.words or [])]
    t = lap(f"transcribe with word times ({len(words)} words)", t)
    turns = speech._finish_separation(proc)
    t = lap(f"wait for speaker separation ({len(turns)} turns)", t)
    segs = model.transcribe  # noqa: F841
    from nodo_api.adapters.local.speech import Word
    tagged = assign_speakers([Word(w.start, w.end, w.word, w.probability) for w in words], turns)
    phrases = build_phrases(tagged, language)
    lap(f"assemble ({len(phrases)} phrases)", t)


main()
