from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from nodo_api.voice.chunker import MAX_CHARS, speech_chunks
from nodo_api.voice.protocol import (
    Ack,
    Hello,
    ProtocolError,
    decode_audio_out,
    encode_audio_frame,
    encode_audio_out,
    parse_audio_frame,
    parse_client_message,
    server_event,
)
from nodo_api.voice.tickets import TicketError, TicketSigner
from tests.voice_support import KEY, pcm


def test_audio_frame_round_trip_and_end_offset():
    parsed = parse_audio_frame(encode_audio_frame(12_500, pcm(100)))

    assert (parsed.offset_ms, parsed.end_ms, len(parsed.pcm)) == (12_500, 12_600, 3200)


@pytest.mark.parametrize("size", [0, 10, 3, 33_000 * 2])
def test_audio_frame_with_bad_size_is_refused(size):
    with pytest.raises(ProtocolError):
        parse_audio_frame(b"\x00\x00\x00\x00" + b"\x00" * size)


def test_assistant_audio_header_round_trip():
    assert decode_audio_out(encode_audio_out(3, 7, b"\x01\x02")) == (3, 7, b"\x01\x02")


def test_client_messages_are_validated_and_extra_fields_refused():
    assert isinstance(parse_client_message('{"type":"ack","sequence":4}'), Ack)
    for bad in (
        '{"type":"ack","sequence":-1}',
        '{"type":"ack","sequence":1,"x":1}',
        '{"type":"nope"}',
        "x",
    ):
        with pytest.raises(ProtocolError):
            parse_client_message(bad)
    with pytest.raises(ProtocolError):
        parse_client_message("{" + " " * 5000 + "}")


def test_hello_with_another_protocol_version_is_refused():
    hello = {"type": "hello", "protocol_version": 2, "ticket": "t" * 30}

    with pytest.raises(ProtocolError, match="unsupported"):
        parse_client_message(json.dumps(hello))
    assert isinstance(parse_client_message(json.dumps(hello | {"protocol_version": 1})), Hello)


def test_reliable_events_have_a_sequence_and_a_derived_id_ephemeral_ones_do_not():
    reliable = server_event("transcript.final", "s1", 7, text="hi")
    ephemeral = server_event("transcript.partial", "s1", None, text="h")

    assert reliable["event_id"] == "s1:7" and reliable["sequence"] == 7
    assert "sequence" not in ephemeral and "event_id" not in ephemeral


def test_ticket_is_bound_to_a_session_and_a_person():
    signer = TicketSigner(KEY.encode(), 60)

    ticket = signer.verify(signer.issue("s1", "u1", "w1"))

    assert (ticket.session_id, ticket.user_id, ticket.workspace_id) == ("s1", "u1", "w1")


def test_ticket_expires_and_rejects_tampering_and_other_keys():
    now = [1000.0]
    signer = TicketSigner(KEY.encode(), 60, clock=lambda: now[0])
    token = signer.issue("s1", "u1", "w1")

    forged = token.split(".")[0] + "." + token.split(".")[1][::-1]
    other = TicketSigner(b"another-key-0123456789-0123456789", 60, clock=lambda: now[0])
    now[0] += 61

    for bad in (token, forged, "garbage", ""):
        with pytest.raises(TicketError):
            signer.verify(bad)
    with pytest.raises(TicketError):
        other.verify(TicketSigner(KEY.encode(), 60, clock=lambda: 1000.0).issue("s1", "u1", "w1"))


def test_ticket_key_must_be_long_enough():
    with pytest.raises(ValueError):
        TicketSigner(b"short", 60)


def test_chunks_cut_at_sentences_and_join_very_short_ones():
    chunks = speech_chunks("Yes. The decision was to move to a queue. Sara owns the benchmark now.")

    assert chunks == ["Yes. The decision was to move to a queue.", "Sara owns the benchmark now."]


def test_a_long_sentence_is_cut_at_commas_then_spaces_and_nothing_is_lost():
    sentence = ", ".join(["a considerably long clause about the ingestion queue"] * 8) + "."

    chunks = speech_chunks(sentence)

    assert all(len(chunk) <= MAX_CHARS for chunk in chunks) and len(chunks) > 1
    assert " ".join(chunks).replace(" ", "") == sentence.replace(" ", "")


def test_empty_answer_has_no_chunks():
    assert speech_chunks("   ") == []


def test_counters_in_client_messages_are_bounded():
    for bad in (
        '{"type":"ack","sequence":100000000000000000000}',
        '{"type":"ptt.start","offset_ms":4294967295}',
        '{"type":"hello","protocol_version":1,"ticket":"'
        + "t" * 30
        + '","last_ack":100000000000000000000}',
    ):
        with pytest.raises(ProtocolError):
            parse_client_message(bad)


def test_an_audio_offset_beyond_any_real_session_is_refused():
    with pytest.raises(ProtocolError, match="invalid_offset"):
        parse_audio_frame(b"\xff\xff\xff\xff" + pcm(100))


def test_on_azure_the_redis_url_must_be_encrypted():
    from types import SimpleNamespace

    from nodo_api.voice.settings import VoiceSettings
    from nodo_api.voice.wiring import _store
    from tests.voice_support import settings as voice_settings

    azure = SimpleNamespace(backend="azure")
    plain = voice_settings().model_copy(update={"redis_url": SecretStr("redis://cache:6379/0")})
    secure = voice_settings().model_copy(update={"redis_url": SecretStr("rediss://cache:6380/0")})

    with pytest.raises(ValueError, match="rediss"):
        _store(azure, plain)
    assert _store(azure, secure).__class__.__name__ == "RedisSessionStore"
    assert isinstance(plain, VoiceSettings)
