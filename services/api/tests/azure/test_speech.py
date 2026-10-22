from __future__ import annotations

import json

import httpx
import pytest

from nodo_api.adapters.azure.speech import (
    API_VERSION,
    AzureSpeechToText,
    SpeechError,
    duration_ms,
    parse_phrases,
)

BASE = "https://speech-test.cognitiveservices.azure.com"
JOB = f"{BASE}/speechtotext/transcriptions/abc?api-version={API_VERSION}"
FILES = f"{BASE}/speechtotext/transcriptions/abc/files?api-version={API_VERSION}"
CONTENT = "https://store.blob.core.windows.net/results/transcription.json?sig=abc"

RESULT = {
    "recognizedPhrases": [
        {
            "speaker": 2,
            "offsetMilliseconds": 3500,
            "durationMilliseconds": 1500,
            "locale": "pl-PL",
            "nBest": [{"display": "Przygotuję benchmark.", "confidence": 0.91}],
        },
        {
            "speaker": 1,
            "offset": "PT1.25S",
            "duration": "PT2S",
            "locale": "it-IT",
            "nBest": [{"display": "Partiamo dalla coda.", "confidence": 0.88}],
        },
        {
            "speaker": 1,
            "offsetMilliseconds": 9000,
            "durationMilliseconds": 10,
            "nBest": [{"display": " "}],
        },
    ]
}


def _client(statuses: list[str], requests: list[httpx.Request], files=None) -> AzureSpeechToText:
    remaining = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(201, json={"self": JOB})
        if request.method == "DELETE":
            return httpx.Response(204)
        if url == JOB:
            status = remaining.pop(0)
            body = {"status": status}
            if status == "Succeeded":
                body["links"] = {"files": FILES}
            return httpx.Response(200, json=body)
        if url == FILES:
            return httpx.Response(
                200,
                json=files
                or {"values": [{"kind": "Transcription", "links": {"contentUrl": CONTENT}}]},
            )
        if url == CONTENT:
            return httpx.Response(200, json=RESULT)
        return httpx.Response(404)

    return AzureSpeechToText(
        BASE,
        lambda: "entra-token",
        httpx.Client(transport=httpx.MockTransport(handler)),
        poll_seconds=0,
        sleep=lambda _: None,
    )


def test_batch_request_asks_for_diarization_language_id_and_a_faithful_transcript() -> None:
    body = AzureSpeechToText.build_request("https://blob/audio?sig=x", ("en-GB", "pl-PL", "it-IT"))
    assert body["locale"] == "en-GB"
    props = body["properties"]
    assert props["diarizationEnabled"] is True
    assert props["languageIdentification"]["candidateLocales"] == ["en-GB", "pl-PL", "it-IT"]
    assert props["profanityFilterMode"] == "None"
    assert props["timeToLiveHours"] == 6


def test_one_locale_skips_language_identification() -> None:
    body = AzureSpeechToText.build_request("u", ("da-DK",))
    assert "languageIdentification" not in body["properties"]


def test_transcribe_submits_polls_fetches_and_cleans_up() -> None:
    requests: list[httpx.Request] = []
    speech = _client(["NotStarted", "Running", "Succeeded"], requests)
    phrases = speech.transcribe("https://blob/audio?sig=x", ("en-GB", "pl-PL"))

    assert [(p.speaker, p.start_ms, p.end_ms, p.text) for p in phrases] == [
        (1, 1250, 3250, "Partiamo dalla coda."),
        (2, 3500, 5000, "Przygotuję benchmark."),
    ]
    assert phrases[0].locale == "it-IT"
    submit = requests[0]
    assert submit.headers["authorization"] == "Bearer entra-token"
    assert json.loads(submit.content)["contentUrls"] == ["https://blob/audio?sig=x"]
    content_request = next(r for r in requests if str(r.url) == CONTENT)
    assert "authorization" not in content_request.headers  # the result link has its own SAS
    assert requests[-1].method == "DELETE"


def test_a_failed_job_raises_and_is_still_deleted() -> None:
    requests: list[httpx.Request] = []
    speech = _client(["Failed"], requests)
    with pytest.raises(SpeechError, match="failed"):
        speech.transcribe("u", ("en-GB",))
    assert requests[-1].method == "DELETE"


def test_a_missing_result_file_is_an_error() -> None:
    speech = _client(["Succeeded"], [], files={"values": [{"kind": "TranscriptionReport"}]})
    with pytest.raises(SpeechError, match="no transcription file"):
        speech.transcribe("u", ("en-GB",))


def test_polling_gives_up_after_the_timeout() -> None:
    speech = _client(["Running"] * 50, [])
    speech._timeout = -1
    with pytest.raises(SpeechError, match="timed out"):
        speech.transcribe("u", ("en-GB",))


def test_at_least_one_locale_is_required() -> None:
    with pytest.raises(SpeechError):
        _client([], []).transcribe("u", ())


def test_durations_accept_numbers_and_iso_strings() -> None:
    assert duration_ms(1200) == 1200
    assert duration_ms("PT1M2.5S") == 62500
    assert duration_ms("PT1H") == 3_600_000
    with pytest.raises(SpeechError):
        duration_ms("soon")


def test_empty_phrases_are_dropped_and_order_is_by_time() -> None:
    phrases = parse_phrases(RESULT)
    assert len(phrases) == 2
    assert [p.start_ms for p in phrases] == sorted(p.start_ms for p in phrases)
