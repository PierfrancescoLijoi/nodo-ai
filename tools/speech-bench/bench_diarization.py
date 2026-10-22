"""Local speaker separation (sherpa-onnx: pyannote segmentation 3.0 + WeSpeaker CAM++) on the corpus.

    python tools/speech-bench/bench_diarization.py [corpus_dir] [models_dir]

Scores with `speaker_accuracy` (see score.py), once with the number of speakers left to the
clustering threshold and once with it fixed at the true value.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import sherpa_onnx
from bench_whisper import load_pcm
from score import speaker_accuracy, speaker_count_error


def diarizer(models: Path, threshold: float, speakers: int = -1) -> sherpa_onnx.OfflineSpeakerDiarization:
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx")
            ),
            num_threads=4,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models / "wespeaker_en_voxceleb_CAM++.onnx"), num_threads=4
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers, threshold=threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def main() -> None:
    corpus = Path(sys.argv[1] if len(sys.argv) > 1 else "tmp/speech-bench")
    default_models = Path(os.environ.get("LOCALAPPDATA", ".")) / "NODO" / "models" / "diarization"
    models = Path(sys.argv[2] if len(sys.argv) > 2 else default_models)
    for wav in sorted(corpus.glob("*.wav")):
        reference = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        truth = [(t["speaker"], t["start"], t["end"]) for t in reference["turns"]]
        audio = load_pcm(wav)
        for label, threshold, speakers in (("auto 0.5", 0.5, -1), ("auto 0.7", 0.7, -1), ("fixed 2", 0.5, 2)):
            started = time.perf_counter()
            result = diarizer(models, threshold, speakers).process(audio).sort_by_start_time()
            elapsed = time.perf_counter() - started
            hyp = [(f"S{r.speaker}", r.start, r.end) for r in result]
            print(
                json.dumps(
                    {
                        "file": wav.stem,
                        "setting": label,
                        "rtf": round(elapsed / (len(audio) / 16000), 3),
                        "speaker_accuracy": round(speaker_accuracy(truth, hyp), 3),
                        "speaker_count_error": speaker_count_error(truth, hyp),
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
