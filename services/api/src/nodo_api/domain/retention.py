"""Retention policies: how long the audio of a meeting is kept (ADR-019).

A person picks the period when they attest a recording (7, 30 or 90 days). The policy id carries
the number of days (`retention-30d`). Audio is the sensitive raw material, so it is what expires;
transcripts, the graph and the rest stay until the person deletes the meeting, which erases
everything. An id that does not carry a number is read as the longest period, never as the shortest:
a mistake must not delete someone's audio early.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

LONGEST_DAYS = 90
_DAYS = re.compile(r"(\d{1,4})d$")


def retention_days(policy_id: str) -> int:
    match = _DAYS.search(policy_id)
    return int(match.group(1)) if match else LONGEST_DAYS


def audio_expires_at(created_at: datetime, policy_id: str) -> datetime:
    return created_at + timedelta(days=retention_days(policy_id))


@dataclass(frozen=True, slots=True)
class AudioDue:
    """A meeting whose audio has outlived its retention period."""

    workspace_id: str
    meeting_id: str
    owner_id: str
    created_at: datetime
    policy_id: str
