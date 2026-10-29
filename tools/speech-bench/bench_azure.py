"""The same corpus and the same scoring as bench_whisper.py, but through the production adapter
(Azure AI Speech batch transcription with diarization and language identification).

    NODO_SPEECH_ENDPOINT=https://<name>.cognitiveservices.azure.com \
        python tools/speech-bench/bench_azure.py urls.json [corpus_dir] [results.jsonl]

`urls.json` maps a corpus name to a read-only SAS URL of that WAV, e.g. {"en": "https://...?sig=..."}
(upload tmp/speech-bench/*.wav to a private container and create 1 hour read links). Authenticates
with `az login` through DefaultAzureCredential, so it needs the "Cognitive Services Speech User"
role. NOT RUN YET: there is no Azure subscription at the time of writing, so this file is untested
against the real service; the scoring it uses is covered by test_score.py.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from azure.identity import DefaultAzureCredential
from bench_whisper import wer, words
from nodo_api.adapters.azure.speech import SPEECH_TOKEN_SCOPE, AzureSpeechToText
from score import speaker_accuracy, speaker_count_error


def main() -> None:
    urls = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    corpus = Path(sys.argv[2] if len(sys.argv) > 2 else "tmp/speech-bench")
    output_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    credential = DefaultAzureCredential()
    speech = AzureSpeechToText(
        os.environ["NODO_SPEECH_ENDPOINT"],
        lambda: credential.get_token(SPEECH_TOKEN_SCOPE).token,
    )
    results = []
    for name, url in urls.items():
        reference = json.loads((corpus / f"{name}.json").read_text(encoding="utf-8"))
        locales = tuple(
            os.environ.get("NODO_CANDIDATE_LOCALES", "en-GB,pl-PL,it-IT,da-DK").split(
                ","
            )
        )
        started = time.perf_counter()
        phrases = speech.transcribe(url, locales)
        elapsed = time.perf_counter() - started
        truth = " ".join(t["text"] for t in reference["turns"])
        text = " ".join(p.text for p in phrases)
        ref_turns = [(t["speaker"], t["start"], t["end"]) for t in reference["turns"]]
        hyp_turns = [
            (f"S{p.speaker}", p.start_ms / 1000, p.end_ms / 1000) for p in phrases
        ]
        result = {
            "service": "azure-speech-batch",
            "file": name,
            "locale": reference.get("locale", name.split("-", 1)[0]),
            "scenario": reference.get("scenario", "clean"),
            "audio_s": round(reference["duration"], 1),
            "wall_s": round(elapsed, 1),
            "rtf": round(elapsed / reference["duration"], 3),
            "locales_detected": sorted({p.locale for p in phrases}),
            "wer": round(wer(words(truth, False), words(text, False)), 3),
            "wer_no_numbers": round(wer(words(truth, True), words(text, True)), 3),
            "speaker_accuracy": round(speaker_accuracy(ref_turns, hyp_turns), 3),
            "speaker_count_error": speaker_count_error(ref_turns, hyp_turns),
            "reference": truth,
            "hypothesis": text,
            "reference_speakers": ref_turns,
            "hypothesis_speakers": hyp_turns,
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
