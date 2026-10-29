"""Speaker separation in its own process.

    python -m nodo_api.adapters.local.diarize_worker <audio> <ffmpeg> <models dir> [threads]

Prints the speaker turns as JSON: [[start, end, speaker], ...]. It runs apart from the engine
because the native library holds Python's global lock for the whole call (eight minutes for an
hour of speech), which froze every request of the engine, and because a crash in native code must
cost one recording a result, not the whole engine its life."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nodo_api.adapters.local.speech import (
    CLUSTER_THRESHOLD,
    SEGMENTATION_DIR,
    SPEAKER_EMBEDDING_FILE,
    SPEAKER_THREADS,
    Turn,
    compact_speech,
    decode_audio,
    restore_compacted_turns,
)

MIN_COMPACTION_SAVINGS = 0.05


def separate(audio_path: str, ffmpeg: str, models: Path, threads: int = SPEAKER_THREADS):
    import sherpa_onnx
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / SEGMENTATION_DIR / "model.onnx")
            ),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models / SPEAKER_EMBEDDING_FILE), num_threads=threads
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    audio = decode_audio(audio_path, ffmpeg)
    chunks = get_speech_timestamps(
        audio,
        VadOptions(
            threshold=0.35,
            min_speech_duration_ms=100,
            min_silence_duration_ms=500,
            speech_pad_ms=250,
        ),
        sampling_rate=16_000,
    )
    compacted, spans = compact_speech(audio, chunks)
    if not spans:
        return []
    use_compacted = len(compacted) <= len(audio) * (1 - MIN_COMPACTION_SAVINGS)
    work = compacted if use_compacted else audio
    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(work).sort_by_start_time()
    turns = [Turn(float(r.start), float(r.end), int(r.speaker)) for r in result]
    if use_compacted:
        turns = restore_compacted_turns(turns, spans)
    return [[round(r.start, 3), round(r.end, 3), r.speaker] for r in turns]


def main(argv: list[str]) -> int:
    audio_path, ffmpeg, models = argv[0], argv[1], Path(argv[2])
    threads = int(argv[3]) if len(argv) > 3 else SPEAKER_THREADS
    json.dump(separate(audio_path, ffmpeg, models, threads), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
