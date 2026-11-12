from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nodo_api.domain.models import (
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.domain.sources import (
    MAX_FRAGMENT_CHARS,
    Fragment,
    Locator,
    Source,
    SourceKind,
)

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def make_fragment(**changes):
    values = {
        "id": "f1",
        "source_id": "src1",
        "section_id": None,
        "ordinal": 0,
        "text": "hello",
        "locator": Locator(page=1),
        "version": 1,
    }
    return Fragment(**{**values, **changes})


def test_locator_requires_a_coordinate():
    with pytest.raises(ValueError):
        Locator()


def test_locator_rejects_reversed_time_range():
    with pytest.raises(ValueError):
        Locator(time_ms=(10, 5))
    with pytest.raises(ValueError):
        Locator(chars=(-1, 5))


def test_locator_rejects_page_zero():
    with pytest.raises(ValueError):
        Locator(page=0)
    with pytest.raises(ValueError):
        Locator(slide=0)


def test_locator_bbox_needs_page():
    with pytest.raises(ValueError):
        Locator(slide=1, bbox=(0.0, 0.0, 1.0, 1.0))
    assert Locator(page=2, bbox=(0.0, 0.0, 1.0, 1.0)).bbox is not None


def test_locator_json_round_trip_is_canonical():
    assert Locator(time_ms=(0, 5)).to_json() == '{"time_ms":[0,5]}'
    full = Locator(page=3, bbox=(0.1, 0.2, 0.3, 0.4), chars=(1, 9))
    assert full.to_json() == '{"bbox":[0.1,0.2,0.3,0.4],"chars":[1,9],"page":3}'
    assert Locator.from_json(full.to_json()) == full


def test_fragment_rejects_empty_text():
    with pytest.raises(ValueError):
        make_fragment(text="   ")


def test_fragment_rejects_text_over_limit():
    make_fragment(text="x" * MAX_FRAGMENT_CHARS)
    with pytest.raises(ValueError):
        make_fragment(text="x" * (MAX_FRAGMENT_CHARS + 1))


def test_fragment_rejects_bad_version_and_ordinal():
    with pytest.raises(ValueError):
        make_fragment(version=0)
    with pytest.raises(ValueError):
        make_fragment(ordinal=-1)


def segment():
    return TranscriptSegment(
        id="seg1",
        meeting_id="m1",
        speaker_id="S1",
        start_ms=100,
        end_ms=900,
        text="we ship on friday",
        confidence=0.9,
        version=2,
        locale=Locale.EN,
        project="p",
    )


def test_fragment_from_segment_keeps_ids_and_time():
    fragment = Fragment.from_segment(segment(), 4)
    assert (fragment.id, fragment.source_id, fragment.ordinal) == ("seg1", "m1", 4)
    assert fragment.locator.time_ms == (100, 900)
    assert fragment.version == 2


def test_fragment_to_segment_round_trip():
    original = segment()
    assert Fragment.from_segment(original, 0).to_segment("p") == original
    with pytest.raises(ValueError):
        make_fragment().to_segment(None)


def test_source_from_meeting_keeps_id_and_state():
    attestation = RecordingAttestation(
        user_id="u1",
        meeting_id="m1",
        notice_version="1",
        acknowledged_at=NOW,
        retention_policy_id="r1",
        locale=Locale.EN,
        audit_event_id="a1",
        signature="sig",
    )
    meeting = Meeting(
        id="m1",
        workspace_id="w1",
        owner_id="u1",
        created_at=NOW,
        state=MeetingState.READY,
        attestation=attestation,
        title="Weekly",
    )
    source = Source.from_meeting(meeting)
    assert (source.id, source.kind, source.state) == ("m1", SourceKind.MEETING, "ready")
    assert (source.workspace_id, source.owner_id, source.title) == ("w1", "u1", "Weekly")
    assert source.created_at == NOW
