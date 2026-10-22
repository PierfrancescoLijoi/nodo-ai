"""Speaker assignment and phrase building (pure functions), plus a real-model run when the models
and the test recordings exist on this machine."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nodo_api.adapters.local.models import GpuArbiter, LocalModelPaths
from nodo_api.adapters.local.speech import (
    LocalSpeechToText,
    Turn,
    Word,
    assign_speakers,
    build_phrases,
)


def _words(spec: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    out, cursor = [], start
    for token in spec.split():
        out.append(Word(cursor, cursor + step * 0.9, f" {token}", 0.9))
        cursor += step
    return out


def test_words_go_to_the_speaker_talking_at_that_moment_and_labels_start_at_one() -> None:
    words = _words("hello there how are you fine thanks")
    turns = [Turn(0.0, 1.7, 7), Turn(1.7, 3.0, 3)]
    tagged = assign_speakers(words, turns)
    assert [label for _, label in tagged] == [1, 1, 1, 1, 2, 2, 2]


def test_a_word_in_a_gap_joins_the_nearest_turn_and_no_turns_means_one_speaker() -> None:
    first, word = Word(0.2, 0.5, " yes", 0.9), Word(5.0, 5.2, " hi", 0.9)
    tagged = assign_speakers([first, word], [Turn(0, 1, 0), Turn(5.5, 6, 1)])
    assert [label for _, label in tagged] == [1, 2]  # the gap word is nearest to the second turn
    assert assign_speakers([word], []) == [(word, 1)]


def test_phrases_follow_speakers_and_pauses() -> None:
    tagged = [(w, 1) for w in _words("good morning everyone")]
    tagged += [(w, 2) for w in _words("thanks sara", start=1.6)]
    tagged += [(w, 2) for w in _words("after a pause", start=10.0)]
    phrases = build_phrases(tagged, "it")
    assert [(p.speaker, p.text) for p in phrases] == [
        (1, "good morning everyone"),
        (2, "thanks sara"),
        (2, "after a pause"),
    ]
    assert phrases[0].start_ms == 0 and phrases[0].end_ms > phrases[0].start_ms
    assert phrases[0].locale == "it" and phrases[0].confidence == pytest.approx(0.9)


def test_a_long_monologue_is_cut_after_a_sentence_end() -> None:
    words = [
        Word(i * 0.3, i * 0.3 + 0.25, f" word{i}{'.' if i % 12 == 11 else ''}", 0.9)
        for i in range(300)
    ]
    phrases = build_phrases([(w, 1) for w in words], "en")
    assert len(phrases) > 2
    assert all(len(p.text) <= 520 for p in phrases)
    assert sum(len(p.text.split()) for p in phrases) == 300


def test_empty_input_gives_no_phrases() -> None:
    assert build_phrases([], "en") == ()


@pytest.mark.parametrize(
    ("probabilities", "expected"),
    [
        ([("sl", 0.5), ("en", 0.3), ("it", 0.1)], "en"),  # Slovenian is not one of ours
        ([("it", 0.2), ("pl", 0.7)], "pl"),
        ([("fr", 0.9)], "da"),  # none of ours: a fixed, predictable fallback
    ],
)
def test_the_language_is_always_one_of_the_supported_four(probabilities, expected) -> None:
    candidates = ("en-GB", "pl-PL", "it-IT", "da-DK")
    assert LocalSpeechToText.pick_language(probabilities, candidates) == expected


# --- real models ----------------------------------------------------------------------------------

CORPUS = Path(__file__).resolve().parents[3] / "tmp" / "speech-bench-2v"
MODELS = LocalModelPaths.discover()
READY = (
    os.environ.get("NODO_TEST_LOCAL_MODELS") == "1"
    and (CORPUS / "en.wav").exists()
    and MODELS.diarization_dir is not None
    and MODELS.ffmpeg is not None
)


@pytest.mark.skipif(not READY, reason="local models or the test recordings are not available")
@pytest.mark.parametrize("language", ["en", "it"])
def test_real_models_transcribe_in_the_right_language_and_separate_two_voices(language) -> None:
    speech = LocalSpeechToText(MODELS, GpuArbiter())
    phrases = speech.transcribe(
        str(CORPUS / f"{language}.wav"), ("en-GB", "pl-PL", "it-IT", "da-DK")
    )
    reference = json.loads((CORPUS / f"{language}.json").read_text(encoding="utf-8"))
    assert {p.locale for p in phrases} == {language}
    assert len({p.speaker for p in phrases}) == 2
    spoken = " ".join(p.text for p in phrases).lower()
    assert "ingest" in spoken
    assert phrases[-1].end_ms / 1000 <= reference["duration"] + 1


def test_only_real_media_files_reach_ffmpeg(tmp_path) -> None:
    from nodo_api.adapters.local.speech import decode_audio, looks_like_media

    webm = tmp_path / "a"
    webm.write_bytes(bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"rest")
    playlist = tmp_path / "b"
    playlist.write_text("#EXTM3U" + chr(10) + "file:///C:/Users/me/secret.txt", encoding="utf-8")
    concat = tmp_path / "c"
    concat.write_text("ffconcat version 1.0" + chr(10) + "file /etc/passwd", encoding="utf-8")
    assert looks_like_media(webm)
    assert not looks_like_media(playlist) and not looks_like_media(concat)
    with pytest.raises(ValueError, match="not an audio"):
        decode_audio(playlist, "ffmpeg")
