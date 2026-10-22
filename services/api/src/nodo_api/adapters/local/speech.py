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
import logging
import subprocess
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


def decode_audio(path: Path | str, ffmpeg: str | None) -> np.ndarray:
    """Any audio or video file to 16 kHz mono float samples. ffmpeg handles every container."""
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not installed")
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
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
        self._diarizer = None
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

    def _load_diarizer(self):
        if self._diarizer is not None or self._models.diarization_dir is None:
            return self._diarizer
        import sherpa_onnx

        root = self._models.diarization_dir
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(root / SEGMENTATION_DIR / "model.onnx")
                ),
                num_threads=4,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(root / SPEAKER_EMBEDDING_FILE), num_threads=4
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1, threshold=CLUSTER_THRESHOLD
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        self._diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        return self._diarizer

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

    def _separate(self, audio: np.ndarray) -> list[Turn]:
        diarizer = self._load_diarizer()
        if diarizer is None:
            logger.warning("speaker_models_missing: every word is attributed to speaker 1")
            return []
        result = diarizer.process(audio).sort_by_start_time()
        return [Turn(r.start, r.end, r.speaker) for r in result]

    # ----------------------------------------------------------------- port

    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]:
        audio = decode_audio(audio_url, self._models.ffmpeg)
        with self._arbiter.lease("speech"):
            language, words = self._transcribe(audio, candidate_locales)
        turns = self._separate(audio)
        return build_phrases(assign_speakers(words, turns), language)
