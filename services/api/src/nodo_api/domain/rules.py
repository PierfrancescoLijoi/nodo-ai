from __future__ import annotations

from dataclasses import replace

from nodo_api.domain.models import Meeting, MeetingState

ALLOWED_TRANSITIONS: dict[MeetingState, frozenset[MeetingState]] = {
    MeetingState.UPLOADING: frozenset({MeetingState.TRANSCRIBING, MeetingState.FAILED}),
    MeetingState.TRANSCRIBING: frozenset({MeetingState.EXTRACTING, MeetingState.FAILED}),
    MeetingState.EXTRACTING: frozenset({MeetingState.REVIEW, MeetingState.FAILED}),
    MeetingState.REVIEW: frozenset({MeetingState.READY, MeetingState.FAILED}),
    MeetingState.READY: frozenset(),
    MeetingState.FAILED: frozenset(),
}


class InvalidStateTransition(ValueError):
    pass


def transition_meeting(meeting: Meeting, target: MeetingState) -> Meeting:
    if target not in ALLOWED_TRANSITIONS[meeting.state]:
        raise InvalidStateTransition(f"Cannot transition {meeting.state} to {target}")
    return replace(meeting, state=target)
