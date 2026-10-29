"""Cut a written answer into pieces a voice can start on while the rest is still being made.

The first sentence is what sets the time to first audio, so the cut is made at sentence ends;
a very long sentence is cut at a comma or a space instead of making the listener wait for it.
"""

from __future__ import annotations

import re

MIN_CHARS = 24  # shorter pieces are joined to the next: "Yes." alone is a click, not speech
MAX_CHARS = 220
_END = re.compile(r"(?<=[.!?…])\s+")
_SOFT = re.compile(r"(?<=[,;:])\s+")


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    pieces: list[str] = []
    current = ""
    for part in _SOFT.split(text):
        if current and len(current) + len(part) + 1 > MAX_CHARS:
            pieces.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        pieces.append(current)
    result: list[str] = []
    for piece in pieces:
        while len(piece) > MAX_CHARS:
            cut = piece.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            result.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            result.append(piece)
    return result


def speech_chunks(answer: str) -> list[str]:
    sentences = [part.strip() for part in _END.split(answer.strip()) if part.strip()]
    chunks: list[str] = []
    pending = ""
    for sentence in sentences:
        pending = f"{pending} {sentence}".strip()
        if len(pending) >= MIN_CHARS:
            chunks.extend(_split_long(pending))
            pending = ""
    if pending:
        if chunks and len(pending) < MIN_CHARS:
            chunks[-1] = f"{chunks[-1]} {pending}"
        else:
            chunks.extend(_split_long(pending))
    return chunks
