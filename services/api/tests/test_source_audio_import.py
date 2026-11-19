"""Imported audio and video (Task 14): a processing confirmation, the meeting speech pipeline and a
30-day retention of the original, on the memory and the SQLite stores.

Every recording here is synthetic (silence written with the wave module) and every phrase is
invented test text; no real recording, name or content."""

from __future__ import annotations

import io
import logging
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.sources import SqliteSourceRepository
from nodo_api.adapters.memory import (
    MemoryBlobStore,
    MemoryMeetingRepository,
    MemoryRetentionStore,
)
from nodo_api.domain.models import Locale, SpokenPhrase
from nodo_api.domain.source_files import looks_like_media
from nodo_api.domain.sources import SourceKind
from nodo_api.errors import ConflictError, NotFoundError, OriginalMissing, ValidationFailed
from nodo_api.services.ingestion import build_segments
from nodo_api.services.retention import RetentionService
from nodo_api.services.source_import import CONFIRMATION_TEXT
from nodo_api.services.sources import SourceService
from tests.test_local_migrations import _insert_document, migrate_to
from tests.test_source_import import OWNER, Stack, _stack

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
PHRASES = (
    SpokenPhrase(1, 0, 2400, "Marco decided to use the queue for ingestion.", 0.97, "en-GB"),
    SpokenPhrase(2, 2500, 5200, "Sara will benchmark retrieval next week.", 0.95, "en-GB"),
)


def wav(frames: int = 1600) -> bytes:
    """A short mono 16 kHz WAV of silence; the frame count makes the bytes unique."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


class Speech:
    """The SpeechToText port: a call log, a hook and a failure switch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.hook: Any = None
        self.fail: Exception | None = None

    def transcribe(self, audio_url: str, candidate_locales: tuple[str, ...]):
        self.calls.append((audio_url, candidate_locales))
        if self.hook is not None:
            self.hook()
        if self.fail is not None:
            raise self.fail
        return PHRASES


@pytest.fixture(params=["memory", "sqlite"])
def stack(request, tmp_path) -> Stack:
    built = _stack(request.param, tmp_path)
    built.speech = Speech()  # type: ignore[attr-defined]
    built.service = built.build(
        speech=built.speech,  # type: ignore[attr-defined]
        speech_locales=("en-GB",),
        clock=lambda: T0,
    )
    yield built
    built.blobs.close()


def upload_audio(stack: Stack, name: str = "standup.wav", data: bytes | None = None):
    source, session = stack.service.create(
        stack.ws, OWNER, filename=name, size_bytes=len(data or wav()), kind=None,
        origin_note=None, idempotency_key=None,
    )  # fmt: skip
    with stack.blobs.open_upload(session.token) as writer:
        writer.write(data or wav())
    stack.service.complete(stack.ws, OWNER, source.id)
    return source


def confirmed_audio(stack: Stack, name: str = "standup.wav", data: bytes | None = None):
    source = upload_audio(stack, name, data)
    stack.service.confirm(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    return source


def events(stack: Stack, kind: str | None = None) -> list[tuple[str, dict[str, str]]]:
    found = list(stack.audit._events.values())
    return [e for e in found if kind is None or e[0] == kind]


def full_deleter(stack: Stack, source_id: str):
    service = SourceService(
        stack.sources, stack.fragments, stack.graph, None, stack.audit, lambda: T0,
        fragment_index=stack.index, blobs=stack.blobs, imports=stack.imports, jobs=stack.queue,
    )  # fmt: skip
    return lambda: service.delete(stack.ws, OWNER, source_id)


def retention(stack: Stack) -> RetentionService:
    return RetentionService(
        MemoryRetentionStore(MemoryMeetingRepository()),
        MemoryBlobStore(),
        stack.audit,
        sources=stack.sources,
        source_blobs=stack.blobs,
    )


def original(stack: Stack, source_id: str) -> Path:
    return stack.blobs.path_for(stack.ws, source_id, 1)


# --------------------------------------------------------------------------- the brief


def test_imported_audio_waits_for_confirmation_before_any_processing(stack: Stack) -> None:
    source = upload_audio(stack)
    assert source.kind is SourceKind.AUDIO
    assert source.retention_until == T0 + timedelta(days=30)
    stored = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert stored.retention_until == T0 + timedelta(days=30)
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert stack.published.keys == []  # nothing for a worker to pick up
    stack.service.process(stack.ws, source.id)  # a stray message is a no-op
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert stack.speech.calls == [] and stack.parser.calls == []  # type: ignore[attr-defined]
    assert stack.fragments.latest(stack.ws, source.id) == ()
    assert stack.service.resume_interrupted() == 0  # the start sweep does not start it either


def test_confirmation_is_audited_without_text_and_starts_transcription(
    stack: Stack, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    assert CONFIRMATION_TEXT == "I may process this recording"
    source = upload_audio(stack, "secret-call.wav")
    job = stack.service.confirm(stack.ws, OWNER, source.id)
    assert job.state == "queued" and len(stack.published.keys) == 1
    (confirmed,) = events(stack, "source_processing_confirmed")
    assert confirmed[1] == {"kind": "audio"}
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert stack.speech.calls == [(str(original(stack, source.id)), ("en-GB",))]  # type: ignore[attr-defined]
    # idempotent: the current state, no second event, no second message
    again = stack.service.confirm(stack.ws, OWNER, source.id)
    assert again.state == "ready" and len(events(stack, "source_processing_confirmed")) == 1
    assert len(stack.published.keys) == 1
    text = caplog.text + repr(events(stack))
    for secret in ("secret-call", "Marco", "queue for ingestion", CONFIRMATION_TEXT):
        assert secret not in text


def test_audio_fragments_have_time_locators_and_meeting_equivalent_extraction(
    stack: Stack,
) -> None:
    source = confirmed_audio(stack)
    fragments = stack.fragments.latest(stack.ws, source.id)
    segments = build_segments(source.id, PHRASES, Locale.EN, None)
    assert [f.id for f in fragments] == [s.id for s in segments]
    assert [f.text for f in fragments] == [s.text for s in segments]
    assert [f.locator.time_ms for f in fragments] == [(0, 2400), (2500, 5200)]
    assert [f.speaker_id for f in fragments] == ["S1", "S2"]
    (root,) = stack.fragments.outline(stack.ws, source.id)
    assert root.parent_id is None and {f.section_id for f in fragments} == {root.id}
    assert stack.index.count_source(stack.ws, source.id) == 2
    hits = stack.index.search(stack.ws, OWNER, "queue ingestion", 5)
    assert hits and hits[0].fragment.source_id == source.id
    evidence = stack.evidence(source.id)
    assert evidence and evidence <= {f.id for f in fragments}
    assert stack.inner.calls == [tuple(f.id for f in fragments)]
    (record,) = stack.fragments.versions(stack.ws, OWNER, source.id)
    assert record.parser == "speech"


def test_original_audio_is_erased_after_30_days_and_fragments_remain(stack: Stack) -> None:
    source = confirmed_audio(stack)
    sweeper = retention(stack)
    assert sweeper.sweep(T0 + timedelta(days=29)).erased == 0
    assert original(stack, source.id).is_file()
    assert sweeper.sweep(T0 + timedelta(days=30)).erased == 1
    assert not original(stack, source.id).exists()
    assert len(stack.fragments.latest(stack.ws, source.id)) == 2
    assert stack.index.search(stack.ws, OWNER, "queue ingestion", 5)
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    assert events(stack, "audio_erased") == [("audio_erased", {"kind": "audio"})]
    # idempotent: nothing is due any more
    assert sweeper.sweep(T0 + timedelta(days=60)).erased == 0
    assert len(events(stack, "audio_erased")) == 1


def test_unfinished_upload_is_not_swept_and_expires_after_completion(stack: Stack) -> None:
    data = wav()
    source, session = stack.service.create(
        stack.ws, OWNER, filename="later.wav", size_bytes=len(data), kind=None,
        origin_note=None, idempotency_key=None,
    )  # fmt: skip
    sweeper = retention(stack)
    assert sweeper.sweep(T0 + timedelta(days=31)).erased == 0
    with stack.blobs.open_upload(session.token) as writer:
        writer.write(data)
    stack.service.complete(stack.ws, OWNER, source.id)
    assert sweeper.sweep(T0 + timedelta(days=31)).erased == 1
    assert not original(stack, source.id).exists()


def test_audit_failure_is_retried_without_losing_the_retention_work(stack: Stack) -> None:
    source = confirmed_audio(stack)
    append = stack.audit.append
    failed_once = False

    def flaky_append(workspace_id, event_type, metadata, *, event_id=None):
        nonlocal failed_once
        if event_type == "audio_erased" and not failed_once:
            failed_once = True
            raise OSError("audit unavailable")
        return append(workspace_id, event_type, metadata, event_id=event_id)

    stack.audit.append = flaky_append  # type: ignore[method-assign]
    sweeper = retention(stack)
    first = sweeper.sweep(T0 + timedelta(days=31))
    assert (first.erased, first.failed) == (0, 1)
    assert not original(stack, source.id).exists()
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).retention_until is not None
    second = sweeper.sweep(T0 + timedelta(days=31))
    assert (second.erased, second.failed) == (1, 0)
    assert len(events(stack, "audio_erased")) == 1


def test_documents_are_never_swept_by_audio_retention(stack: Stack) -> None:
    document = stack.imported("plan.md", b"plan v1")
    audio = upload_audio(stack)  # awaiting confirmation still counts its 30 days
    result = retention(stack).sweep(T0 + timedelta(days=3650))
    assert result.erased == 1
    assert original(stack, document.id).is_file()
    assert not original(stack, audio.id).exists()
    assert stack.sources.get_owned(stack.ws, OWNER, document.id).retention_until is None


def test_confirm_by_another_user_is_404(stack: Stack) -> None:
    source = upload_audio(stack)
    with pytest.raises(NotFoundError):
        stack.service.confirm(stack.ws, "u2", source.id)
    with pytest.raises(NotFoundError):
        stack.service.confirm("ws-other", OWNER, source.id)
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert events(stack, "source_processing_confirmed") == []


def test_media_with_wrong_extension_is_sniffed_by_content(stack: Stack) -> None:
    good = confirmed_audio(stack, "voice-memo.mp3", wav(800))  # WAV bytes behind an .mp3 name
    assert stack.job(good.id).state == "ready"
    bad = upload_audio(stack, "fake.wav", b"just some text, not a recording")
    stack.service.confirm(stack.ws, OWNER, bad.id)
    stack.service.process(stack.ws, bad.id)
    job = stack.job(bad.id)
    assert (job.state, job.error_code) == ("failed", "unsupported")
    assert len(stack.speech.calls) == 1  # type: ignore[attr-defined]  # only the real recording


# --------------------------------------------------------------------------- decisions


def test_kind_follows_the_extension_and_audio_needs_a_speech_engine(stack: Stack) -> None:
    for n, name in enumerate(("a.mp3", "b.wav", "c.m4a", "d.flac", "e.ogg", "f.mp4", "g.mkv",
                              "h.mov", "i.webm")):  # fmt: skip
        source, _ = stack.service.create(
            stack.ws, OWNER, filename=name, size_bytes=n + 1, kind=None, origin_note=None,
            idempotency_key=None,
        )  # fmt: skip
        assert source.kind is SourceKind.AUDIO
    with pytest.raises(ValidationFailed):
        stack.service.create(
            stack.ws, OWNER, filename="a.pdf", size_bytes=1, kind=SourceKind.AUDIO,
            origin_note=None, idempotency_key=None,
        )  # fmt: skip
    plain = stack.build()  # no speech engine: audio is refused at create
    with pytest.raises(ValidationFailed):
        plain.create(
            stack.ws, OWNER, filename="talk.wav", size_bytes=1, kind=None, origin_note=None,
            idempotency_key=None,
        )  # fmt: skip


def test_delete_erases_the_original_at_once(stack: Stack) -> None:
    waiting = upload_audio(stack, data=wav(100))
    ready = confirmed_audio(stack, data=wav(200))
    for source in (ready, waiting):  # the ready one first: nothing_left searches every source
        full_deleter(stack, source.id)()
        assert not original(stack, source.id).exists()
        stack.nothing_left(source.id)
        with pytest.raises(NotFoundError):
            stack.service.confirm(stack.ws, OWNER, source.id)


def test_cancel_while_awaiting_ends_cancelled_and_erases_the_original(stack: Stack) -> None:
    source = upload_audio(stack)
    job = stack.service.cancel(stack.ws, OWNER, source.id)
    assert job.state == "cancelled"
    assert not original(stack, source.id).exists()
    with pytest.raises(ConflictError):
        stack.service.confirm(stack.ws, OWNER, source.id)
    with pytest.raises(OriginalMissing) as missing:
        stack.service.retry(stack.ws, OWNER, source.id)
    assert missing.value.code == "original_missing"
    assert stack.speech.calls == []  # type: ignore[attr-defined]


def test_cancel_before_confirmation_retries_when_file_erasure_fails(stack: Stack) -> None:
    source = upload_audio(stack)
    erase = stack.blobs.erase_originals
    failed_once = False

    def flaky_erase(workspace_id: str, source_id: str) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("disk busy")
        erase(workspace_id, source_id)

    stack.blobs.erase_originals = flaky_erase  # type: ignore[method-assign]
    with pytest.raises(OSError):
        stack.service.cancel(stack.ws, OWNER, source.id)
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "cancelling"
    assert stack.service.cancel(stack.ws, OWNER, source.id).state == "cancelled"
    assert not original(stack, source.id).exists()
    assert len(events(stack, "source_import_cancelled")) == 1


def test_cancel_reservation_cannot_be_claimed_by_retention(stack: Stack) -> None:
    source = upload_audio(stack)
    erase = stack.blobs.erase_originals
    swept_during_cancel = None

    def erase_then_sweep(workspace_id: str, source_id: str) -> None:
        nonlocal swept_during_cancel
        erase(workspace_id, source_id)
        swept_during_cancel = retention(stack).sweep(T0 + timedelta(days=31))

    stack.blobs.erase_originals = erase_then_sweep  # type: ignore[method-assign]
    assert stack.service.cancel(stack.ws, OWNER, source.id).state == "cancelled"
    assert swept_during_cancel is not None
    assert (swept_during_cancel.erased, swept_during_cancel.failed) == (0, 0)
    stored = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert stored.state == "cancelled" and stored.retention_until is None
    assert retention(stack).sweep(T0 + timedelta(days=365)).erased == 0
    assert events(stack, "audio_erased") == []


def test_confirm_of_a_document_is_a_conflict(stack: Stack) -> None:
    document = stack.imported("plan.md", b"plan v1")
    with pytest.raises(ConflictError):
        stack.service.confirm(stack.ws, OWNER, document.id)


def test_a_retry_asks_for_a_new_confirmation(stack: Stack) -> None:
    stack.speech.fail = ValueError("The recording could not be decoded")  # type: ignore[attr-defined]
    source = confirmed_audio(stack)
    job = stack.job(source.id)
    assert (job.state, job.error_code) == ("failed", "damaged")
    stack.speech.fail = None  # type: ignore[attr-defined]
    assert stack.service.retry(stack.ws, OWNER, source.id).state == "awaiting_confirmation"
    stack.service.process(stack.ws, source.id)
    assert len(stack.speech.calls) == 1  # type: ignore[attr-defined]
    stack.service.confirm(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert len(events(stack, "source_processing_confirmed")) == 2


def test_retry_after_retention_erased_the_original_is_original_missing(stack: Stack) -> None:
    stack.speech.fail = ValueError("The recording could not be decoded")  # type: ignore[attr-defined]
    source = confirmed_audio(stack)
    assert stack.job(source.id).state == "failed"
    assert retention(stack).sweep(T0 + timedelta(days=31)).erased == 1
    with pytest.raises(OriginalMissing):
        stack.service.retry(stack.ws, OWNER, source.id)
    assert stack.job(source.id).state == "failed"


def test_a_late_confirmation_after_retention_is_original_missing(stack: Stack) -> None:
    source = upload_audio(stack)
    retention(stack).sweep(T0 + timedelta(days=31))
    with pytest.raises(OriginalMissing):
        stack.service.confirm(stack.ws, OWNER, source.id)
    assert events(stack, "source_processing_confirmed") == []


def test_a_new_recording_can_be_reimported_after_retention(stack: Stack, tmp_path) -> None:
    source = confirmed_audio(stack)
    retention(stack).sweep(T0 + timedelta(days=31))
    for data in (wav(), wav(3200)):  # the same recording again, then a new one
        new_file = tmp_path / "standup-2.wav"
        new_file.write_bytes(data)
        job = stack.service.reimport(stack.ws, OWNER, source.id, new_file)
        assert job.state == "awaiting_confirmation"
        stack.service.confirm(stack.ws, OWNER, source.id)
        stack.service.process(stack.ws, source.id)
        assert stack.job(source.id).state == "ready"
        assert original(stack, source.id).is_file()
        retention(stack).sweep(T0 + timedelta(days=62))


def test_sweep_racing_a_delete_writes_no_event(stack: Stack) -> None:
    source = confirmed_audio(stack)
    delete = full_deleter(stack, source.id)
    erase = stack.blobs.erase_originals

    def erase_then_delete(workspace_id: str, source_id: str) -> None:
        erase(workspace_id, source_id)
        delete()

    stack.blobs.erase_originals = erase_then_delete  # type: ignore[method-assign]
    result = retention(stack).sweep(T0 + timedelta(days=31))
    assert (result.erased, result.failed) == (0, 0)
    assert events(stack, "audio_erased") == []
    assert not original(stack, source.id).exists()


def test_sweep_claim_prevents_a_concurrent_reimport_from_being_erased(
    stack: Stack, tmp_path
) -> None:
    source = confirmed_audio(stack)
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(wav(3200))
    erase = stack.blobs.erase_originals
    raced = False

    def erase_while_reimporting(workspace_id: str, source_id: str) -> None:
        nonlocal raced
        with pytest.raises(ConflictError):
            stack.service.reimport(stack.ws, OWNER, source.id, replacement)
        raced = True
        erase(workspace_id, source_id)

    stack.blobs.erase_originals = erase_while_reimporting  # type: ignore[method-assign]
    assert retention(stack).sweep(T0 + timedelta(days=31)).erased == 1
    assert raced and not original(stack, source.id).exists()
    stack.blobs.erase_originals = erase  # type: ignore[method-assign]
    assert stack.service.reimport(stack.ws, OWNER, source.id, replacement).state == (
        "awaiting_confirmation"
    )
    assert original(stack, source.id).is_file()


@pytest.mark.parametrize("stop", ["delete", "cancel"])
def test_a_stop_during_transcription_writes_nothing(stack: Stack, stop: str) -> None:
    source = upload_audio(stack)
    stack.service.confirm(stack.ws, OWNER, source.id)
    if stop == "delete":
        stack.speech.hook = full_deleter(stack, source.id)  # type: ignore[attr-defined]
    else:
        stack.speech.hook = lambda: stack.service.cancel(stack.ws, OWNER, source.id)  # type: ignore[attr-defined]
    stack.service.process(stack.ws, source.id)
    stack.nothing_left(source.id)
    if stop == "cancel":
        assert stack.job(source.id).state == "cancelled"
    else:
        assert not original(stack, source.id).exists()


# --------------------------------------------------------------------------- HTTP


def _audio_over_http(app, client, headers) -> str:
    data = wav()
    created = client.post(
        "/v1/sources",
        headers={**headers, "Idempotency-Key": "audio-key-1"},
        json={"filename": "standup.wav", "size_bytes": len(data)},
    ).json()
    assert created["source"]["kind"] == "audio"
    assert client.put(created["upload_url"], content=data).status_code == 204
    source_id = created["source"]["id"]
    done = client.post(f"/v1/sources/{source_id}/complete", headers=headers)
    assert done.json()["import"]["state"] == "awaiting_confirmation"
    return source_id


def test_confirm_route_owner_only_and_idempotent(app, client, auth_headers) -> None:
    source_id = _audio_over_http(app, client, auth_headers)
    url = f"/v1/sources/{source_id}/confirm-processing"
    app.state.container.auth.add_dev_token("bob-token-0123456789", "user-b", "workspace-a")
    bob = {"Authorization": "Bearer bob-token-0123456789"}
    assert client.post(url, headers=bob, json={"confirmed": True}).status_code == 404
    assert client.post(url, headers=auth_headers, json={"confirmed": False}).status_code == 422
    first = client.post(url, headers=auth_headers, json={"confirmed": True})
    second = client.post(url, headers=auth_headers, json={"confirmed": True})
    assert first.status_code == second.status_code == 202
    assert first.json()["import"]["state"] == second.json()["import"]["state"] == "queued"
    app.state.container.source_import.process("workspace-a", source_id)
    status = client.get(f"/v1/sources/{source_id}/import", headers=auth_headers)
    assert status.json()["import"]["state"] == "ready"


def test_confirm_route_is_501_on_azure_and_needs_authentication(app, client, auth_headers) -> None:
    anonymous = client.post("/v1/sources/x/confirm-processing", json={"confirmed": True})
    assert anonymous.status_code == 401
    app.state.container.source_import = None
    response = client.post(
        "/v1/sources/x/confirm-processing", headers=auth_headers, json={"confirmed": True}
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"


def test_original_missing_is_a_coded_409(app, client, auth_headers) -> None:
    source_id = _audio_over_http(app, client, auth_headers)
    assert client.post(f"/v1/sources/{source_id}/cancel", headers=auth_headers).status_code == 202
    response = client.post(f"/v1/sources/{source_id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "original_missing"


# --------------------------------------------------------------------------- review fixes


@pytest.mark.parametrize("start", ["awaiting", "ready"])
def test_a_complete_replay_during_the_reimport_copy_transcribes_nothing(
    stack: Stack, tmp_path, start: str
) -> None:
    source = upload_audio(stack) if start == "awaiting" else confirmed_audio(stack)
    calls = len(stack.speech.calls)  # type: ignore[attr-defined]
    new_file = tmp_path / "standup-2.wav"
    new_file.write_bytes(wav(3200))
    adopt = stack.blobs.adopt
    seen: list[str] = []

    def replay_while_copying(workspace_id, source_id, version, path):
        stored = adopt(workspace_id, source_id, version, path)
        stack.service.complete(stack.ws, OWNER, source.id)  # the owner client retries
        stack.service.process(stack.ws, source.id)
        seen.append(stack.job(source.id).state)
        with pytest.raises(ConflictError):  # nothing to confirm until the copy is done
            stack.service.confirm(stack.ws, OWNER, source.id)
        return stored

    stack.blobs.adopt = replay_while_copying  # type: ignore[method-assign]
    job = stack.service.reimport(stack.ws, OWNER, source.id, new_file)
    assert seen == ["awaiting_confirmation"]
    assert job.state == "awaiting_confirmation" and job.step is None
    assert len(stack.speech.calls) == calls  # type: ignore[attr-defined]
    confirmed = 0 if start == "awaiting" else 1
    assert len(events(stack, "source_processing_confirmed")) == confirmed


def test_a_restart_during_the_reimport_copy_transcribes_nothing(stack: Stack, tmp_path) -> None:
    source = confirmed_audio(stack)
    new_file = tmp_path / "standup-2.wav"
    new_file.write_bytes(wav(3200))
    adopt = stack.blobs.adopt
    resumed: list[int] = []

    def restart_after_copy(workspace_id, source_id, version, path):
        stored = adopt(workspace_id, source_id, version, path)
        # What a crash here leaves, seen by the next process: the start sweep, then a delivery.
        resumed.append(stack.service.resume_interrupted())
        stack.service.process(stack.ws, source.id)
        assert stack.job(source.id).state == "awaiting_confirmation"
        return stored

    stack.blobs.adopt = restart_after_copy  # type: ignore[method-assign]
    stack.service.reimport(stack.ws, OWNER, source.id, new_file)
    assert resumed == [0]
    assert len(stack.speech.calls) == 1  # type: ignore[attr-defined]  # the first import only
    assert stack.job(source.id).state == "awaiting_confirmation"


def test_a_crash_after_the_reimport_copy_waits_and_can_be_recovered(stack: Stack, tmp_path) -> None:
    source = confirmed_audio(stack)
    new_file = tmp_path / "standup-2.wav"
    new_file.write_bytes(wav(3200))
    append = stack.audit.append

    class Crash(BaseException):
        pass

    def crash_on_reimport(workspace_id, event_type, metadata, *, event_id=None):
        if event_type == "source_reimported":
            raise Crash  # the new hash is written, the process dies before anything else
        return append(workspace_id, event_type, metadata, event_id=event_id)

    stack.audit.append = crash_on_reimport  # type: ignore[method-assign]
    with pytest.raises(Crash):
        stack.service.reimport(stack.ws, OWNER, source.id, new_file)
    stack.audit.append = append  # type: ignore[method-assign]
    assert stack.service.resume_interrupted() == 0
    stack.service.complete(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert len(stack.speech.calls) == 1  # type: ignore[attr-defined]
    # The owner imports the same file again: it now waits for a confirmation that works.
    job = stack.service.reimport(stack.ws, OWNER, source.id, new_file)
    assert (job.state, job.step) == ("awaiting_confirmation", None)
    stack.service.confirm(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert len(stack.speech.calls) == 2  # type: ignore[attr-defined]
    assert len(events(stack, "source_processing_confirmed")) == 2


def test_the_sweep_skips_a_recording_whose_import_is_running(stack: Stack) -> None:
    source = upload_audio(stack)
    stack.service.confirm(stack.ws, OWNER, source.id)  # queued, not yet processed
    sweeper = retention(stack)
    assert sweeper.sweep(T0 + timedelta(days=31)).erased == 0
    assert original(stack, source.id).is_file()
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"
    assert stack.sources.get_owned(stack.ws, OWNER, source.id).state == "ready"
    assert sweeper.sweep(T0 + timedelta(days=31)).erased == 1


def test_a_file_that_is_not_media_is_refused_before_asking(stack: Stack, tmp_path) -> None:
    bad = upload_audio(stack, "fake.wav", b"just some text, not a recording")
    job = stack.job(bad.id)
    assert (job.state, job.error_code) == ("failed", "unsupported")
    assert stack.sources.get_owned(stack.ws, OWNER, bad.id).state == "failed"
    assert events(stack, "source_processing_confirmed") == []
    good = upload_audio(stack)
    text = tmp_path / "notes.wav"
    text.write_bytes(b"#EXTM3U\nfile:///etc/passwd\n")
    with pytest.raises(ValidationFailed):
        stack.service.reimport(stack.ws, OWNER, good.id, text)
    assert stack.job(good.id).state == "awaiting_confirmation"
    assert stack.speech.calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "head",
    [
        b"ID3\x04\x00",
        b"\xff\xfb\x90\x00",
        b"\xff\xfa\x90\x00",
        b"\xff\xf3\x90\x00",
        b"\xff\xf2\x90\x00",
        b"\xff\xe3\x90\x00",
        b"\xff\xe2\x90\x00",
        b"\xff\xf1\x50\x80",
        b"\xff\xf9\x50\x80",
        b"\x00\x00\x00\x20ftypqt  ",
        b"\x00\x00\x00\x08wide",
        b"\x00\x00\x10\x00mdat",
        b"\x00\x00\x00\x6cmoov",
        b"\x00\x00\x00\x08free",
        b"\x00\x00\x00\x08skip",
        b"RIFF\x24\x00\x00\x00WAVE",
    ],
)
def test_media_signatures_cover_common_recordings(tmp_path, head: bytes) -> None:
    path = tmp_path / "clip"
    path.write_bytes(head + bytes(32))
    assert looks_like_media(path)


@pytest.mark.parametrize(
    "head",
    [
        b"",
        b"\xff\xfeh\x00i\x00",  # UTF-16 text
        b"#EXTM3U\n",
        b"ffconcat version 1.0\n",
        b"abcdfree text",  # a box name inside text is not a box
        b"long mdat text",
    ],
)
def test_text_is_not_media(tmp_path, head: bytes) -> None:
    path = tmp_path / "clip"
    path.write_bytes(head)
    assert not looks_like_media(path)


def test_an_audit_failure_on_confirm_processes_nothing_and_can_be_retried(stack: Stack) -> None:
    source = upload_audio(stack)
    append = stack.audit.append

    def failing(workspace_id, event_type, metadata, *, event_id=None):
        if event_type == "source_processing_confirmed":
            raise OSError("audit unavailable")
        return append(workspace_id, event_type, metadata, event_id=event_id)

    stack.audit.append = failing  # type: ignore[method-assign]
    with pytest.raises(OSError):
        stack.service.confirm(stack.ws, OWNER, source.id)
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "awaiting_confirmation"
    assert stack.published.keys == [] and stack.speech.calls == []  # type: ignore[attr-defined]
    stack.audit.append = append  # type: ignore[method-assign]
    assert stack.service.confirm(stack.ws, OWNER, source.id).state == "queued"
    stack.service.process(stack.ws, source.id)
    assert stack.job(source.id).state == "ready"


def test_the_same_recording_uploaded_twice_is_not_asked_or_processed_twice(stack: Stack) -> None:
    data = wav(400)
    first = upload_audio(stack, "a.wav", data)
    second = upload_audio(stack, "b.wav", data)
    assert stack.job(first.id).state == "awaiting_confirmation"
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, second.id)
    assert stack.published.keys == []
    stack.service.confirm(stack.ws, OWNER, first.id)
    stack.service.process(stack.ws, first.id)
    third = upload_audio(stack, "c.wav", data)  # once processed: the ready job answers
    assert stack.job(first.id).state == "ready"
    with pytest.raises(NotFoundError):
        stack.sources.get_owned(stack.ws, OWNER, third.id)
    assert len(stack.speech.calls) == 1  # type: ignore[attr-defined]
    assert len(events(stack, "source_processing_confirmed")) == 1


def test_a_crash_while_erasing_is_finished_by_the_next_sweep(stack: Stack) -> None:
    source = confirmed_audio(stack)
    erase = stack.blobs.erase_originals

    def crash(workspace_id: str, source_id: str) -> None:
        raise OSError("disk busy")

    stack.blobs.erase_originals = crash  # type: ignore[method-assign]
    sweeper = retention(stack)
    first = sweeper.sweep(T0 + timedelta(days=31))
    assert (first.erased, first.failed) == (0, 1)
    assert original(stack, source.id).is_file()
    stack.blobs.erase_originals = erase  # type: ignore[method-assign]
    second = sweeper.sweep(T0 + timedelta(days=31))
    assert (second.erased, second.failed) == (1, 0)
    assert not original(stack, source.id).exists()
    stored = stack.sources.get_owned(stack.ws, OWNER, source.id)
    assert stored.retention_until is None and stored.state == "ready"
    assert len(events(stack, "audio_erased")) == 1
    assert sweeper.sweep(T0 + timedelta(days=62)).erased == 0


def test_migration_0012_keeps_existing_rows_without_a_retention_date(tmp_path) -> None:
    path = tmp_path / "nodo.db"
    old = migrate_to(path, 11)
    _insert_document(old, "d1")
    _insert_document(old, "a1", kind="audio")
    old.close()
    db = LocalDatabase(path)
    try:
        with db.transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT id, retention_until, retention_erase_phase FROM sources ORDER BY id"
            ).fetchall()
            index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("sources_retention_idx",),
            ).fetchone()
            applied = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    finally:
        db.close()
    assert [tuple(r) for r in rows] == [("a1", None, None), ("d1", None, None)]
    assert index is not None and "retention_until IS NOT NULL" in index[0]
    assert 12 in applied
    db = LocalDatabase(path)
    try:
        assert SqliteSourceRepository(db).originals_due(T0 + timedelta(days=9999), 10) == ()
    finally:
        db.close()
