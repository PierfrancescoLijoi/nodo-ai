from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    Speaker,
)
from nodo_api.domain.rules import InvalidStateTransition, transition_meeting


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (MeetingState.UPLOADING, MeetingState.TRANSCRIBING),
        (MeetingState.TRANSCRIBING, MeetingState.EXTRACTING),
        (MeetingState.EXTRACTING, MeetingState.REVIEW),
        (MeetingState.REVIEW, MeetingState.READY),
        (MeetingState.UPLOADING, MeetingState.FAILED),
        (MeetingState.TRANSCRIBING, MeetingState.FAILED),
        (MeetingState.EXTRACTING, MeetingState.FAILED),
    ],
)
def test_allowed_state_transitions(start: MeetingState, target: MeetingState) -> None:
    meeting = _meeting(start)
    assert transition_meeting(meeting, target).state is target


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (MeetingState.UPLOADING, MeetingState.READY),
        (MeetingState.READY, MeetingState.UPLOADING),
        (MeetingState.FAILED, MeetingState.TRANSCRIBING),
    ],
)
def test_invalid_state_transitions_are_rejected(start: MeetingState, target: MeetingState) -> None:
    with pytest.raises(InvalidStateTransition):
        transition_meeting(_meeting(start), target)


def test_domain_models_are_immutable() -> None:
    meeting = _meeting(MeetingState.UPLOADING)
    with pytest.raises(FrozenInstanceError):
        meeting.state = MeetingState.READY  # type: ignore[misc]
    assert replace(meeting, state=MeetingState.TRANSCRIBING).state is MeetingState.TRANSCRIBING


def test_speaker_label_must_be_anonymous() -> None:
    with pytest.raises(ValueError, match="anonymous"):
        Speaker(id="speaker-1", meeting_id="m1", anon_label="Marco")
    assert Speaker(id="speaker-1", meeting_id="m1", anon_label="S1").anon_label == "S1"


def _meeting(state: MeetingState) -> Meeting:
    attestation = RecordingAttestation(
        user_id="user-a",
        meeting_id="m1",
        notice_version="v1",
        acknowledged_at=datetime.now(UTC),
        retention_policy_id="r1",
        locale=Locale.EN,
        audit_event_id="audit-1",
        signature="signature",
    )
    return Meeting(
        id="m1",
        workspace_id="workspace-a",
        owner_id="user-a",
        created_at=datetime.now(UTC),
        state=state,
        attestation=attestation,
    )
