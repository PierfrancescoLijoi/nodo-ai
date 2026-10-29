"""Build the voice runtime from the settings and the application's own services."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi.concurrency import run_in_threadpool

from nodo_api.config import Settings
from nodo_api.domain.models import Locale
from nodo_api.resilience import Bulkhead
from nodo_api.services.query import QueryRequest
from nodo_api.voice.assistant import Assistant
from nodo_api.voice.finalizer import MeetingTranscriptFinalizer
from nodo_api.voice.hub import SessionHub, VoiceRuntime
from nodo_api.voice.live import Deps
from nodo_api.voice.settings import VoiceSettings
from nodo_api.voice.speech import BufferedRecognizer, SpeechSynthesizer, StreamingRecognizer
from nodo_api.voice.store import MemorySessionStore, SessionRecord, SessionStore
from nodo_api.voice.tickets import TicketSigner

RecognizerFactory = Callable[[SessionRecord], StreamingRecognizer]
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def _store(settings: Settings, voice: VoiceSettings) -> SessionStore:
    if voice.redis_url is not None:
        from nodo_api.voice.redis_store import RedisSessionStore

        if settings.backend == "azure" and not voice.redis_url.get_secret_value().startswith(
            "rediss://"
        ):
            # Finals are meeting text: on Azure they never cross the network in clear.
            raise ValueError("NODO_VOICE_REDIS_URL must use rediss:// on Azure")
        return RedisSessionStore.from_url(
            voice.redis_url.get_secret_value(), voice.redis_max_connections
        )
    if settings.backend == "azure":
        # Several replicas share sessions through Redis; memory would silently split them.
        raise ValueError("NODO_VOICE_REDIS_URL is required for the voice gateway on Azure")
    return MemorySessionStore()


def _azure_parts(settings: Settings, voice: VoiceSettings):
    from azure.identity import DefaultAzureCredential

    from nodo_api.voice.azure_speech import AzureStreamingRecognizer, AzureSynthesizer

    if not settings.azure_speech_endpoint:
        raise ValueError("NODO_AZURE_SPEECH_ENDPOINT is required for the Azure voice path")
    credential = DefaultAzureCredential()

    def token() -> str:
        return credential.get_token(COGNITIVE_SCOPE).token

    endpoint = settings.azure_speech_endpoint

    def recognizer(record: SessionRecord) -> StreamingRecognizer:
        return AzureStreamingRecognizer(endpoint, token, diarize=record.mode == "meeting")

    synthesizer = AzureSynthesizer(endpoint, token) if voice.synthesizer == "azure" else None
    return recognizer, synthesizer


def _local_recognizer(settings: Settings, container: Any) -> RecognizerFactory:
    # Push to talk shares the computer with everything else: bound the transcriptions in flight.
    gate = Bulkhead("voice-speech", settings.bulkhead_speech, wait_seconds=2.0)

    def make(record: SessionRecord) -> StreamingRecognizer:
        def transcribe(path: str):
            with gate:
                return container.speech.transcribe(path, (record.locale,))

        return BufferedRecognizer(transcribe)

    return make


def build_voice(
    settings: Settings,
    voice: VoiceSettings,
    container: Any,
    *,
    store: SessionStore | None = None,
    recognizer_factory: RecognizerFactory | None = None,
    synthesizer: SpeechSynthesizer | None = None,
) -> VoiceRuntime:
    """Explicit arguments replace the provider choices (tests and the load generator use this)."""
    assert voice.ticket_key is not None
    if recognizer_factory is None:
        if voice.recognizer == "local":
            recognizer_factory = _local_recognizer(settings, container)
        else:
            recognizer_factory, azure_synth = _azure_parts(settings, voice)
            synthesizer = synthesizer or azure_synth

    async def ask(workspace_id: str, user_id: str, question: str, locale: str) -> Any:
        def run() -> Any:
            request = QueryRequest(question, Locale(locale))
            gate = container.query_gate
            if gate is None:
                return container.query_service.query(workspace_id, user_id, request)
            with gate:
                return container.query_service.query(workspace_id, user_id, request)

        return await run_in_threadpool(run)

    deps = Deps(
        store=store or _store(settings, voice),
        settings=voice,
        replica_id=uuid.uuid4().hex,
        recognizer_factory=recognizer_factory,
        assistant=Assistant(ask, synthesizer),
        finalizer=MeetingTranscriptFinalizer(
            container.transcripts, container.meetings, container.jobs
        ),
    )
    signer = TicketSigner(voice.ticket_key.get_secret_value().encode(), voice.ticket_ttl_seconds)
    return VoiceRuntime(deps, signer, SessionHub(deps))
