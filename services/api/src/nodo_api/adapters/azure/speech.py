"""Azure AI Speech batch transcription with diarization and language identification.

Authentication is an Entra bearer token for the Speech resource (custom subdomain endpoint).
Audio is read by the service from a short-lived read-only SAS link. The finished job is deleted
after the result is fetched; transcripts live only in our own database."""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from nodo_api.domain.models import SpeechPoll, SpeechStatus, SpokenPhrase

API_VERSION = "2024-11-15"
SPEECH_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
MAX_CANDIDATE_LOCALES = 10
_ISO_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


class SpeechError(RuntimeError):
    pass


def duration_ms(value: Any) -> int:
    """Milliseconds from either a number or an ISO 8601 duration such as PT1M2.5S."""
    if isinstance(value, int | float):
        return int(value)
    match = _ISO_DURATION.fullmatch(str(value))
    if not match:
        raise SpeechError("Unreadable time in the speech result")
    hours, minutes, seconds = (float(part) if part else 0.0 for part in match.groups())
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def parse_phrases(result: dict[str, Any]) -> tuple[SpokenPhrase, ...]:
    phrases = []
    for item in result.get("recognizedPhrases", []):
        best = (item.get("nBest") or [{}])[0]
        text = (best.get("display") or "").strip()
        if not text:
            continue
        start = duration_ms(item.get("offsetMilliseconds", item.get("offset", 0)))
        length = duration_ms(item.get("durationMilliseconds", item.get("duration", 0)))
        phrases.append(
            SpokenPhrase(
                speaker=int(item.get("speaker", 1)),
                start_ms=start,
                end_ms=start + length,
                text=text,
                confidence=float(best.get("confidence", item.get("confidence", 0.0))),
                locale=str(item.get("locale", "")),
            )
        )
    return tuple(sorted(phrases, key=lambda p: (p.start_ms, p.speaker)))


class AzureSpeechToText:
    def __init__(
        self,
        endpoint: str,
        token_provider: Callable[[], str],
        http: httpx.Client | None = None,
        poll_seconds: float = 15.0,
        timeout_seconds: float = 3300.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = endpoint.rstrip("/")
        self._token = token_provider
        self._http = http or httpx.Client(timeout=60.0)
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    @staticmethod
    def build_request(audio_url: str, locales: tuple[str, ...]) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "diarizationEnabled": True,
            "wordLevelTimestampsEnabled": False,
            "punctuationMode": "DictatedAndAutomatic",
            "profanityFilterMode": "None",  # a transcript must say what was said
            "timeToLiveHours": 6,
        }
        if len(locales) >= 2:
            properties["languageIdentification"] = {
                "candidateLocales": list(locales[:MAX_CANDIDATE_LOCALES]),
                "mode": "Continuous",
            }
        return {
            "contentUrls": [audio_url],
            "locale": locales[0],
            "displayName": "nodo-ingestion",
            "properties": properties,
        }

    # The same job in three calls, so a worker can submit, leave, and collect later.

    def submit(self, audio_url: str, candidate_locales: tuple[str, ...]) -> str:
        if not candidate_locales:
            raise SpeechError("At least one candidate locale is required")
        return self._submit(audio_url, candidate_locales)

    def poll(self, handle: str) -> SpeechPoll:
        self._check_handle(handle)
        response = self._http.get(handle, headers=self._headers())
        response.raise_for_status()
        body = response.json()
        status = body.get("status")
        if status == "Succeeded":
            result = self._fetch_result(str(body["links"]["files"]))
            return SpeechPoll(SpeechStatus.SUCCEEDED, parse_phrases(result))
        if status == "Failed":
            return SpeechPoll(SpeechStatus.FAILED)
        return SpeechPoll(SpeechStatus.RUNNING)

    def cancel(self, handle: str) -> None:
        self._check_handle(handle)
        self._delete(handle)

    def _check_handle(self, handle: str) -> None:
        """The handle comes back from our own database, but it is followed with a bearer token:
        it must still point at this resource's transcription jobs and nowhere else."""
        if not handle.startswith(f"{self._base}/speechtotext/transcriptions/"):
            raise SpeechError("Unexpected speech job address")

    def transcribe(
        self, audio_url: str, candidate_locales: tuple[str, ...]
    ) -> tuple[SpokenPhrase, ...]:
        if not candidate_locales:
            raise SpeechError("At least one candidate locale is required")
        job_url = self._submit(audio_url, candidate_locales)
        try:
            files_url = self._wait(job_url)
            return parse_phrases(self._fetch_result(files_url))
        finally:
            self._delete(job_url)

    def _submit(self, audio_url: str, locales: tuple[str, ...]) -> str:
        response = self._http.post(
            f"{self._base}/speechtotext/transcriptions:submit",
            params={"api-version": API_VERSION},
            headers=self._headers(),
            json=self.build_request(audio_url, locales),
        )
        response.raise_for_status()
        return str(response.json()["self"])

    def _wait(self, job_url: str) -> str:
        deadline = time.monotonic() + self._timeout
        while True:
            response = self._http.get(job_url, headers=self._headers())
            response.raise_for_status()
            body = response.json()
            status = body.get("status")
            if status == "Succeeded":
                return str(body["links"]["files"])
            if status == "Failed":
                raise SpeechError("Speech transcription failed")
            if time.monotonic() > deadline:
                raise SpeechError("Speech transcription timed out")
            self._sleep(self._poll)

    def _fetch_result(self, files_url: str) -> dict[str, Any]:
        files = self._http.get(files_url, headers=self._headers())
        files.raise_for_status()
        for item in files.json().get("values", []):
            if item.get("kind") == "Transcription":
                # The content link carries its own SAS token: no bearer header here.
                content = self._http.get(item["links"]["contentUrl"])
                content.raise_for_status()
                return content.json()
        raise SpeechError("The speech result has no transcription file")

    def _delete(self, job_url: str) -> None:
        # Best effort: the six hour time to live removes the job anyway.
        with contextlib.suppress(httpx.HTTPError):
            self._http.delete(job_url, headers=self._headers())
