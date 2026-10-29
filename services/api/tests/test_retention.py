from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.repos import SqliteAuditLog, SqliteMeetingRepository
from nodo_api.adapters.local.retention import SqliteRetentionStore
from nodo_api.adapters.memory import MemoryRetentionStore
from nodo_api.domain.models import Locale, Meeting, MeetingState, RecordingAttestation
from nodo_api.domain.retention import LONGEST_DAYS, audio_expires_at, retention_days
from nodo_api.services.retention import RetentionService

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("policy", "days"),
    [("retention-7d", 7), ("retention-30d", 30), ("retention-90d", 90), ("weird", 90), ("", 90)],
)
def test_the_policy_id_carries_the_days_and_a_mistake_means_the_longest_period(
    policy, days
) -> None:
    assert retention_days(policy) == days == (LONGEST_DAYS if policy in ("weird", "") else days)


def test_audio_expires_exactly_the_chosen_number_of_days_after_the_recording() -> None:
    created = datetime(2026, 10, 1, tzinfo=UTC)
    assert audio_expires_at(created, "retention-7d") == created + timedelta(days=7)


def _meeting(meeting_id: str, age_days: int, policy: str, workspace: str = "ws") -> Meeting:
    created = NOW - timedelta(days=age_days)
    return Meeting(
        id=meeting_id,
        workspace_id=workspace,
        owner_id="alice",
        created_at=created,
        state=MeetingState.READY,
        attestation=RecordingAttestation(
            "alice", meeting_id, "2026-10-v1", created, policy, Locale.EN, "audit-1", "sig"
        ),
    )


class Blobs:
    def __init__(self, fail_for: str | None = None) -> None:
        self.erased: list[tuple[str, str]] = []
        self.fail_for = fail_for

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        if meeting_id == self.fail_for:
            raise OSError("storage unavailable")
        self.erased.append((workspace_id, meeting_id))


class Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def append(self, workspace_id: str, event_type: str, metadata: dict) -> str:
        self.events.append((workspace_id, event_type, metadata))
        return "event"


def _memory(*meetings: Meeting):
    from nodo_api.adapters.memory import MemoryMeetingRepository

    repo = MemoryMeetingRepository()
    for index, meeting in enumerate(meetings):
        repo.add(meeting.workspace_id, meeting, f"key-{index}", "h")
    return repo, MemoryRetentionStore(repo)


def test_each_meeting_expires_by_its_own_policy_not_a_global_one() -> None:
    _, store = _memory(
        _meeting("short", 8, "retention-7d"),  # past its 7 days
        _meeting("medium", 8, "retention-30d"),  # well within 30
        _meeting("long", 31, "retention-90d"),  # within 90
        _meeting("old", 91, "retention-90d"),  # past 90
    )
    blobs, audit = Blobs(), Audit()
    result = RetentionService(store, blobs, audit).sweep(NOW)
    assert sorted(m for _, m in blobs.erased) == ["old", "short"]
    assert result.erased == 2 and result.failed == 0
    assert {(e[1], e[2]["meeting_id"]) for e in audit.events} == {
        ("audio_expired", "old"),
        ("audio_expired", "short"),
    }


def test_a_swept_meeting_is_not_swept_again() -> None:
    _, store = _memory(_meeting("a", 10, "retention-7d"))
    blobs = Blobs()
    service = RetentionService(store, blobs, Audit())
    assert service.sweep(NOW).erased == 1
    assert service.sweep(NOW).erased == 0 and len(blobs.erased) == 1


def test_a_failure_on_one_meeting_does_not_stop_the_others_and_is_retried_next_time() -> None:
    _, store = _memory(_meeting("bad", 10, "retention-7d"), _meeting("good", 10, "retention-7d"))
    blobs = Blobs(fail_for="bad")
    service = RetentionService(store, blobs, Audit())
    result = service.sweep(NOW)
    assert (result.erased, result.failed) == (1, 1)
    assert [m for _, m in blobs.erased] == ["good"]
    blobs.fail_for = None
    assert service.sweep(NOW).erased == 1  # "bad" was not marked, so it comes back


def test_a_meeting_is_marked_only_after_its_audio_is_gone() -> None:
    _, store = _memory(_meeting("a", 10, "retention-7d"))
    marked = []
    original = store.mark_audio_erased
    store.mark_audio_erased = lambda *args: (marked.append(args), original(*args))[1]
    RetentionService(store, Blobs(fail_for="a"), Audit()).sweep(NOW)
    assert marked == []


def test_deleted_meetings_are_left_alone() -> None:
    repo, store = _memory(_meeting("gone", 10, "retention-7d"))
    repo.save("ws", replace(repo.get_owned("ws", "alice", "gone"), tombstoned_at=NOW))
    assert store.audio_due(NOW, 10) == ()


def test_a_backlog_is_cleared_in_batches() -> None:
    _, store = _memory(*[_meeting(f"m{i}", 10, "retention-7d") for i in range(7)])
    blobs = Blobs()
    service = RetentionService(store, blobs, Audit())
    first = service.sweep(NOW, limit=3)
    assert first.erased == 3 and first.more
    assert RetentionService(store, Blobs(), Audit()).sweep_all(NOW).erased == 4


# --- SQLite (the local backend) ------------------------------------------------------------------


def test_the_local_store_applies_the_same_rule(tmp_path) -> None:
    db = LocalDatabase(tmp_path / "n.db")
    meetings = SqliteMeetingRepository(db)
    meetings.add("ws", _meeting("short", 8, "retention-7d"), "k1", "h")
    meetings.add("ws", _meeting("fresh", 1, "retention-7d"), "k2", "h")
    store = SqliteRetentionStore(db)
    assert [d.meeting_id for d in store.audio_due(NOW, 10)] == ["short"]
    blobs, audit = Blobs(), SqliteAuditLog(db)
    assert RetentionService(store, blobs, audit).sweep(NOW).erased == 1
    assert store.audio_due(NOW, 10) == ()
    db.close()
