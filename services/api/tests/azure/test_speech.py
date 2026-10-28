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
from nodo_api.domain.models import SpeechStatus

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


# --- in stages: submit, poll, cancel (the worker never waits inside one call) -----------------


def test_submit_returns_the_job_handle_at_once_and_does_not_poll() -> None:
    requests: list[httpx.Request] = []
    speech = _client(["Succeeded"], requests)
    handle = speech.submit("https://blob/audio?sig=x", ("en-GB", "pl-PL"))
    assert handle == JOB
    assert [r.method for r in requests] == ["POST"]


def test_poll_reports_running_without_fetching_anything_else() -> None:
    requests: list[httpx.Request] = []
    speech = _client(["NotStarted", "Running"], requests)
    assert speech.poll(JOB).status is SpeechStatus.RUNNING
    assert speech.poll(JOB).status is SpeechStatus.RUNNING
    assert {r.method for r in requests} == {"GET"} and len(requests) == 2


def test_poll_returns_the_phrases_when_the_job_has_succeeded() -> None:
    speech = _client(["Succeeded"], [])
    result = speech.poll(JOB)
    assert result.status is SpeechStatus.SUCCEEDED
    assert [(p.speaker, p.text) for p in result.phrases] == [
        (1, "Partiamo dalla coda."),
        (2, "Przygotuję benchmark."),
    ]


def test_poll_reports_a_failed_job_instead_of_raising() -> None:
    result = _client(["Failed"], []).poll(JOB)
    assert result.status is SpeechStatus.FAILED and result.phrases == ()


def test_cancel_deletes_the_job_and_never_raises() -> None:
    requests: list[httpx.Request] = []
    _client([], requests).cancel(JOB)
    assert [r.method for r in requests] == ["DELETE"]

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    offline = AzureSpeechToText(
        BASE, lambda: "t", httpx.Client(transport=httpx.MockTransport(broken))
    )
    offline.cancel(JOB)  # best effort: the service removes the job by itself after six hours


@pytest.mark.parametrize(
    "handle",
    [
        "https://evil.example/speechtotext/transcriptions/abc",
        f"{BASE}/other/path",
        f"{BASE}.evil.example/speechtotext/transcriptions/abc",
        "file:///etc/passwd",
    ],
)
def test_a_handle_pointing_anywhere_else_is_refused_before_a_token_is_sent(handle) -> None:
    requests: list[httpx.Request] = []
    speech = _client(["Succeeded"], requests)
    with pytest.raises(SpeechError, match="address"):
        speech.poll(handle)
    with pytest.raises(SpeechError, match="address"):
        speech.cancel(handle)
    assert requests == []
