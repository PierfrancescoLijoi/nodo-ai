"""Build the test corpus: one WAV per conversation plus a JSON with the exact reference.

    python tools/speech-bench/make_corpus.py [out_dir] [--two-voices] [--languages en,it]

Windows only (SAPI voices installed on the machine). Output is 16 kHz mono PCM, 0.5 s of silence
between turns, and `<lang>.json` with the reference text and the true speaker turns.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import wave
from pathlib import Path

from conversations import CONVERSATIONS, TWO_VOICES

RATE = 16000
# Passed with -Command (not as a script file) so the machine's script execution policy is untouched.
SYNTH = (
    "Add-Type -AssemblyName System.Speech;"
    "$jobs = Get-Content -Raw -Encoding UTF8 '{jobs}' | ConvertFrom-Json;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "foreach ($j in $jobs) { $s.SelectVoice($j.voice); $s.Rate = [int]$j.rate;"
    "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono');"
    "$s.SetOutputToWaveFile($j.out, $f); $s.Speak($j.text); $s.SetOutputToNull() }"
)
GAP_SECONDS = 0.5


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == RATE and handle.getnchannels() == 1
        return handle.readframes(handle.getnframes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", nargs="?", default="tmp/speech-bench")
    parser.add_argument("--two-voices", action="store_true")
    parser.add_argument("--languages", default=",")
    args = parser.parse_args()
    out = Path(args.out_dir)
    available = TWO_VOICES if args.two_voices else CONVERSATIONS
    requested = [item.strip() for item in args.languages.split(",") if item.strip()]
    unknown = set(requested) - set(available)
    if unknown:
        parser.error(f"unknown languages: {', '.join(sorted(unknown))}")
    conversations = {
        key: value
        for key, value in available.items()
        if not requested or key in requested
    }
    work = out / "turns"
    work.mkdir(parents=True, exist_ok=True)
    jobs = []
    for lang, conv in conversations.items():
        for index, (speaker, text) in enumerate(conv["turns"]):
            jobs.append(
                {
                    "voice": conv["voices"][speaker],
                    "rate": 0,
                    "text": text,
                    "out": str((work / f"{lang}-{index:02d}.wav").resolve()),
                    "pitch": conv.get("b_pitch", 1.0) if speaker == "B" else 1.0,
                }
            )
    job_file = out / "jobs.json"
    job_file.write_text(json.dumps(jobs), encoding="utf-8")
    subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-STA",
            "-Command",
            SYNTH.replace("{jobs}", str(job_file.resolve())),
        ],
        check=True,
    )
    for job in jobs:
        if job["pitch"] == 1.0:
            continue
        source = Path(job["out"])
        pitched = source.with_name(source.stem + "-pitched.wav")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                f"rubberband=pitch={job['pitch']}",
                str(pitched),
            ],
            check=True,
        )
        pitched.replace(source)

    gap = b"\x00\x00" * int(RATE * GAP_SECONDS)
    for lang, conv in conversations.items():
        pcm, cursor, turns = bytearray(), 0.0, []
        for index, (speaker, text) in enumerate(conv["turns"]):
            audio = read_wav(work / f"{lang}-{index:02d}.wav")
            seconds = len(audio) / 2 / RATE
            turns.append(
                {
                    "speaker": speaker,
                    "start": cursor,
                    "end": cursor + seconds,
                    "text": text,
                }
            )
            pcm += audio + gap
            cursor += seconds + GAP_SECONDS
        with wave.open(str(out / f"{lang}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(bytes(pcm))
        reference = {"locale": conv["locale"], "duration": cursor, "turns": turns}
        (out / f"{lang}.json").write_text(
            json.dumps(reference, ensure_ascii=False, indent=1), "utf-8"
        )
        print(f"{lang}: {cursor:.1f} s, {len(turns)} turns")


if __name__ == "__main__":
    main()
