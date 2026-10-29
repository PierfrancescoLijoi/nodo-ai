"""Local speech to text: Whisper for the words, a speaker model for who says them.

The result has the same shape Azure Speech batch transcription gives the ingestion service:
phrases with a speaker number, start and end, text, confidence and language. Where Azure does it in
one call, this adapter runs three steps on the machine:

1. decode the recording with ffmpeg (any container the browser or a phone produces),
2. pick the language among the candidate locales and transcribe with word timestamps,
3. separate the speakers (sherpa-onnx: pyannote segmentation + a speaker embedding model) and
   give every word to the speaker who was talking at that moment.

Known differences from Azure, documented in ADR-014: one language per recording (no switching
inside a meeting), and speaker quality depends on a small embedding model.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nodo_api.adapters.local.models import GpuArbiter, LocalModelPaths
from nodo_api.domain.models import SpokenPhrase

logger = logging.getLogger("nodo_speech")

SAMPLE_RATE = 16_000
MAX_WORD_GAP_SECONDS = 1.2
MAX_PHRASE_SECONDS = 25.0
MAX_PHRASE_CHARS = 450
SENTENCE_END = (".", "?", "!", "…")
SPEAKER_EMBEDDING_FILE = "3dspeaker_eres2net.onnx"
SEGMENTATION_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
CLUSTER_THRESHOLD = 0.6
SEPARATION_TIMEOUT_SECONDS = 6 * 3600
SPEAKER_THREADS = min(8, max(2, (os.cpu_count() or 4) // 2))  # 8 threads: 25 % faster than 4


@dataclass(frozen=True, slots=True)
class Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True, slots=True)
class Turn:
    start: float
    end: float
    speaker: int


@dataclass(frozen=True, slots=True)
class SpeechSpan:
    """One retained speech interval in compacted and original time."""

    compact_start: float
    compact_end: float
    original_start: float
    original_end: float


def compact_speech(
    audio: np.ndarray, chunks: Sequence[dict[str, int]]
) -> tuple[np.ndarray, tuple[SpeechSpan, ...]]:
    """Remove VAD-confirmed silence while retaining an exact timeline conversion."""
    pieces: list[np.ndarray] = []
    spans: list[SpeechSpan] = []
    compact_samples = 0
    previous_end = 0
    for chunk in chunks:
        start = max(previous_end, int(chunk["start"]), 0)
        end = min(int(chunk["end"]), len(audio))
        if end <= start:
            continue
        length = end - start
        pieces.append(audio[start:end])
        spans.append(
            SpeechSpan(
                compact_samples / SAMPLE_RATE,
                (compact_samples + length) / SAMPLE_RATE,
                start / SAMPLE_RATE,
                end / SAMPLE_RATE,
            )
        )
        compact_samples += length
        previous_end = end
    compacted = np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)
    return compacted, tuple(spans)


def restore_compacted_turns(turns: Sequence[Turn], spans: Sequence[SpeechSpan]) -> list[Turn]:
    """Split turns at removed gaps and restore their positions in the original recording."""
    restored: list[Turn] = []
    for turn in turns:
        for span in spans:
            start = max(turn.start, span.compact_start)
            end = min(turn.end, span.compact_end)
            if end <= start:
                continue
            restored.append(
                Turn(
                    span.original_start + start - span.compact_start,
                    span.original_start + end - span.compact_start,
                    turn.speaker,
                )
            )
    return restored


def _overlap(word: Word, turn: Turn) -> float:
    return max(0.0, min(word.end, turn.end) - max(word.start, turn.start))


def _distance(word: Word, turn: Turn) -> float:
    middle = (word.start + word.end) / 2
    return (
        0.0
        if turn.start <= middle <= turn.end
        else min(abs(middle - turn.start), abs(middle - turn.end))
    )


def assign_speakers(words: Sequence[Word], turns: Sequence[Turn]) -> list[tuple[Word, int]]:
    """Each word goes to the turn it overlaps most, else to the nearest turn; labels are renumbered
    1, 2, 3 in order of first appearance (the labels a model invents mean nothing)."""
    if not turns:
        return [(word, 1) for word in words]
    numbering: dict[int, int] = {}
    tagged = []
    for word in words:
        best = max(turns, key=lambda t: (_overlap(word, t), -_distance(word, t)))
        label = numbering.setdefault(best.speaker, len(numbering) + 1)
        tagged.append((word, label))
    return tagged


def build_phrases(tagged: Sequence[tuple[Word, int]], language: str) -> tuple[SpokenPhrase, ...]:
    """Group consecutive words of one speaker into phrases, cutting at long pauses and at length
    limits (preferably after the end of a sentence)."""
    phrases: list[SpokenPhrase] = []
    group: list[Word] = []
    speaker = 0

    def flush() -> None:
        if not group:
            return
        text = "".join(w.text for w in group).strip()
        if text:
            phrases.append(
                SpokenPhrase(
                    speaker=speaker,
                    start_ms=round(group[0].start * 1000),
                    end_ms=max(round(group[-1].end * 1000), round(group[0].start * 1000)),
                    text=text,
                    confidence=float(np.mean([w.probability for w in group])),
                    locale=language,
                )
            )
        group.clear()

    for word, label in tagged:
        if group:
            gap = word.start - group[-1].end
            length = sum(len(w.text) for w in group)
            long_enough = length > MAX_PHRASE_CHARS * 0.6 and group[-1].text.rstrip().endswith(
                SENTENCE_END
            )
            too_long = length > MAX_PHRASE_CHARS or word.end - group[0].start > MAX_PHRASE_SECONDS
            if label != speaker or gap > MAX_WORD_GAP_SECONDS or long_enough or too_long:
                flush()
        speaker = label
        group.append(word)
    flush()
    return tuple(phrases)


# Signatures of the audio and video containers a recording can be in. Anything else (a playlist, a
# concat script, text that ffmpeg would interpret as a list of other files) is refused before ffmpeg
# sees it: the upload is an untrusted file.
CONTAINER_SIGNATURES = (
    (0, b"RIFF"),  # WAV
    (0, b"OggS"),
    (0, bytes([0x1A, 0x45, 0xDF, 0xA3])),  # Matroska and WebM
    (0, b"fLaC"),
    (0, b"ID3"),
    (4, b"ftyp"),  # MP4 and M4A
    (0, bytes([0xFF, 0xFB])),  # MP3 frame
    (0, bytes([0xFF, 0xF3])),
    (0, bytes([0xFF, 0xF1])),  # AAC (ADTS)
)


def looks_like_media(path: Path | str) -> bool:
    with Path(path).open("rb") as handle:
        head = handle.read(16)
    return any(head[offset : offset + len(sig)] == sig for offset, sig in CONTAINER_SIGNATURES)


def decode_audio(path: Path | str, ffmpeg: str | None) -> np.ndarray:
    """Any audio or video file to 16 kHz mono float samples. ffmpeg handles every container."""
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not installed")
    if not looks_like_media(path):
        raise ValueError("The recording is not an audio or video file")
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",  # no network, no pipes, no other protocols, whatever the file says
            "-i",
            str(path),
            "-vn",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise ValueError("The recording could not be decoded")
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def ensure_cuda_libraries() -> None:
    """CTranslate2 needs the CUDA 12 runtime DLLs; when they ship inside torch, make them visible."""
    try:
        import os

        import torch

        os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))
    except (ImportError, OSError, AttributeError):
        pass


class LocalSpeechToText:
    def __init__(self, models: LocalModelPaths, arbiter: GpuArbiter) -> None:
        self._models = models
        self._arbiter = arbiter
        self._whisper = None
        self._batched = None
        self._lock = threading.Lock()
        arbiter.register("speech", self.unload)

    # -------------------------------------------------------------- models

    def _load_whisper(self):
        if self._whisper is not None:
            return self._whisper
        ensure_cuda_libraries()
        import ctranslate2
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        gpu = ctranslate2.get_cuda_device_count() > 0
        self._whisper = WhisperModel(
            self._models.whisper_model,
            device="cuda" if gpu else "cpu",
            compute_type="float16" if gpu else "int8",
            local_files_only=True,
        )
        self._batched = BatchedInferencePipeline(model=self._whisper) if gpu else None
        return self._whisper

    def unload(self) -> None:
        with self._lock:
            self._whisper = None
            self._batched = None
        gc.collect()

    # --------------------------------------------------------------- steps

    @staticmethod
    def pick_language(probabilities: Sequence[tuple[str, float]], candidates: Sequence[str]) -> str:
        """The most likely of the languages this app supports, never a language outside them."""
        wanted = {c.split("-")[0].lower() for c in candidates}
        allowed = [(lang, p) for lang, p in probabilities if lang in wanted]
        if not allowed:
            return sorted(wanted)[0]
        return max(allowed, key=lambda item: item[1])[0]

    def _transcribe(self, audio: np.ndarray, candidates: Sequence[str]) -> tuple[str, list[Word]]:
        model = self._load_whisper()
        _, _, probabilities = model.detect_language(audio, language_detection_segments=4)
        language = self.pick_language(probabilities, candidates)
        options = {"language": language, "beam_size": 5, "word_timestamps": True}
        if self._batched is not None:
            segments, _ = self._batched.transcribe(audio, batch_size=8, **options)
        else:
            segments, _ = model.transcribe(
                audio, vad_filter=True, condition_on_previous_text=False, **options
            )
        words = [
            Word(w.start, w.end, w.word, w.probability)
            for segment in segments
            for w in (segment.words or [])
        ]
        return language, words

    def _start_separation(self, audio_path: str) -> subprocess.Popen | None:
        """Start speaker separation in a separate process (see diarize_worker.py); it runs while the
        words are transcribed, so the two do not add up."""
        if self._models.diarization_dir is None or self._models.ffmpeg is None:
            logger.warning("speaker_models_missing: every word is attributed to speaker 1")
            return None
        command = [
            sys.executable,
            "-m",
            "nodo_api.adapters.local.diarize_worker",
            str(audio_path),
            self._models.ffmpeg,
            str(self._models.diarization_dir),
        ]
        return subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _finish_separation(process: subprocess.Popen | None) -> list[Turn]:
        if process is None:
            return []
        try:
            output, _ = process.communicate(timeout=SEPARATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            logger.warning("speaker_separation_timed_out")
            return []
        if process.returncode != 0:
            logger.warning("speaker_separation_failed code=%s", process.returncode)
            return []  # one speaker beats losing the transcript
        try:
            return [Turn(start, end, speaker) for start, end, speaker in json.loads(output)]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("speaker_separation_invalid_output")
            return []

    # ----------------------------------------------------------------- port

    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]:
        separation = self._start_separation(audio_url)
        try:
            # The worker decodes independently; starting it first overlaps both decodes too.
            audio = decode_audio(audio_url, self._models.ffmpeg)
            with self._arbiter.lease("speech"):
                language, words = self._transcribe(audio, candidate_locales)
            turns = self._finish_separation(separation)
        except BaseException:
            if separation is not None and separation.poll() is None:
                separation.kill()
            raise
        return build_phrases(assign_speakers(words, turns), language)
