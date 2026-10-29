"""Wire protocol of the voice gateway (docs/VOICE-REALTIME-ARCHITECTURE.md section 3).

Audio travels as binary frames: a four byte big-endian offset in milliseconds, then 16 kHz mono
PCM16. The offset is the position of the frame in the recording, not a counter and not a clock:
after a reconnect the client resends from the offset the server acknowledged and the server drops
what it already has, so a resume never duplicates or loses audio. Control and transcript events
are versioned JSON; reliable server events carry a sequence the client acknowledges.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

PROTOCOL_VERSION = 1
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
BYTES_PER_MS = INPUT_SAMPLE_RATE * 2 // 1000  # PCM16 mono
MIN_FRAME_BYTES = 10 * BYTES_PER_MS
MAX_FRAME_BYTES = 1000 * BYTES_PER_MS
MAX_TEXT_MESSAGE = 4096
MAX_COUNTER = 2**31 - 1  # offsets and sequences: far beyond a session, safe in Redis
AUDIO_OUT_KIND = 2  # first byte of a binary message from the server

_OFFSET = struct.Struct("!I")
_AUDIO_OUT = struct.Struct("!BII")  # kind, turn, chunk index


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AudioFrame:
    offset_ms: int
    pcm: bytes

    @property
    def end_ms(self) -> int:
        return self.offset_ms + len(self.pcm) // BYTES_PER_MS


def parse_audio_frame(data: bytes) -> AudioFrame:
    if len(data) < _OFFSET.size + MIN_FRAME_BYTES:
        raise ProtocolError("frame.too_short")
    pcm = data[_OFFSET.size :]
    if len(pcm) > MAX_FRAME_BYTES or len(pcm) % 2:
        raise ProtocolError("frame.invalid_size")
    offset = _OFFSET.unpack_from(data)[0]
    if offset > MAX_COUNTER:
        raise ProtocolError("frame.invalid_offset")
    return AudioFrame(offset, pcm)


def encode_audio_frame(offset_ms: int, pcm: bytes) -> bytes:
    return _OFFSET.pack(offset_ms) + pcm


def encode_audio_out(turn: int, index: int, pcm: bytes) -> bytes:
    return _AUDIO_OUT.pack(AUDIO_OUT_KIND, turn, index) + pcm


def decode_audio_out(data: bytes) -> tuple[int, int, bytes]:
    kind, turn, index = _AUDIO_OUT.unpack_from(data)
    if kind != AUDIO_OUT_KIND:
        raise ProtocolError("frame.unknown_kind")
    return turn, index, data[_AUDIO_OUT.size :]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Hello(_Message):
    type: Literal["hello"]
    protocol_version: int
    ticket: str = Field(min_length=20, max_length=1024)
    last_ack: int = Field(default=0, ge=0, le=MAX_COUNTER)


class Ack(_Message):
    type: Literal["ack"]
    sequence: int = Field(ge=0, le=MAX_COUNTER)


class PttStart(_Message):
    type: Literal["ptt.start"]
    offset_ms: int = Field(ge=0, le=MAX_COUNTER)


class PttEnd(_Message):
    type: Literal["ptt.end"]


class BargeIn(_Message):
    type: Literal["barge_in"]


class Ping(_Message):
    type: Literal["ping"]


class End(_Message):
    type: Literal["end"]


ClientMessage = Annotated[
    Hello | Ack | PttStart | PttEnd | BargeIn | Ping | End, Field(discriminator="type")
]
_CLIENT = TypeAdapter(ClientMessage)


def parse_client_message(text: str) -> Hello | Ack | PttStart | PttEnd | BargeIn | Ping | End:
    if len(text) > MAX_TEXT_MESSAGE:
        raise ProtocolError("message.too_large")
    try:
        message = _CLIENT.validate_json(text)
    except ValidationError as error:
        raise ProtocolError("message.invalid") from error
    if isinstance(message, Hello) and message.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError("protocol.unsupported_version")
    return message


def server_event(
    type_: str, session_id: str, sequence: int | None = None, **fields: Any
) -> dict[str, Any]:
    """Reliable events carry a sequence (and the id derived from it, which makes a replay
    idempotent for the client); ephemeral ones, such as a partial transcript, carry neither."""
    event: dict[str, Any] = {
        "type": type_,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
    }
    if sequence is not None:
        event["sequence"] = sequence
        event["event_id"] = f"{session_id}:{sequence}"
    event.update(fields)
    return event
