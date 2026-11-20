"""Behaviour every import job store must show (Task 5, ADR-029).

The memory and SQLite stores run this suite. Each test class provides the fixtures `sources`,
`imports` and `ws`, plus `seed_meeting(ws, owner, meeting_id)`."""

from __future__ import annotations

from dataclasses import replace

import pytest

from nodo_api.domain.sources import CLEAR, SourceKind
from nodo_api.errors import ConflictError, NotFoundError
from tests.source_contract import OTHER, OWNER, T0, _race, source


class ImportContract:
    def test_create_is_idempotent_per_source(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        first = imports.create(ws, OWNER, "d1")
        assert (first.state, first.step, first.done, first.total) == ("queued", None, 0, 0)
        assert (first.attempt, first.error_code, first.cancel_requested) == (0, None, False)
        assert first.source_id == "d1" and first.owner_id == OWNER and first.workspace_id == ws
        imports.update(ws, "d1", state="parsing", done=2, total=5)
        again = imports.create(ws, OWNER, "d1")
        # a second create hands back the job as it stands, it does not reset it
        assert (again.state, again.done, again.total) == ("parsing", 2, 5)
        assert again.created_at == first.created_at

    def test_other_user_gets_not_found(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        with pytest.raises(NotFoundError) as other:
            imports.get_owned(ws, OTHER, "d1")
        with pytest.raises(NotFoundError) as missing:
            imports.get_owned(ws, OWNER, "nope")
        assert other.value.public_message == missing.value.public_message
        with pytest.raises(NotFoundError):
            imports.request_cancel(ws, OTHER, "d1")
        with pytest.raises(NotFoundError):
            imports.create(ws, OTHER, "d1")
        assert imports.is_cancel_requested(ws, "d1") is False  # nothing leaked through

    def test_create_needs_a_live_source_that_is_not_a_meeting(
        self, sources, imports, seed_meeting, ws
    ) -> None:
        seed_meeting(ws, OWNER, "m1")
        gone = sources.add(ws, source("d2", ws=ws))
        sources.save(ws, replace(gone, tombstoned_at=T0))
        for source_id in ("m1", "d2", "unknown"):
            with pytest.raises(NotFoundError):
                imports.create(ws, OWNER, source_id)

    def test_update_progress_and_state(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        created = imports.create(ws, OWNER, "d1")
        parsing = imports.update(ws, "d1", state="parsing", step="parse", done=1, total=4)
        assert (parsing.state, parsing.step, parsing.done, parsing.total) == (
            "parsing",
            "parse",
            1,
            4,
        )
        assert parsing.updated_at >= created.updated_at
        # fields not passed stay as they were
        indexing = imports.update(ws, "d1", state="indexing", attempt=2)
        assert (indexing.step, indexing.done, indexing.total, indexing.attempt) == (
            "parse",
            1,
            4,
            2,
        )
        failed = imports.update(ws, "d1", state="failed", error_code="encrypted")
        assert (failed.state, failed.error_code) == ("failed", "encrypted")
        # CLEAR sets the step and the error code back to nothing, for a retry
        retry = imports.update(ws, "d1", state="queued", step=CLEAR, error_code=CLEAR)
        assert (retry.state, retry.step, retry.error_code) == ("queued", None, None)
        assert imports.get_owned(ws, OWNER, "d1") == retry
        assert imports.get_for_processing(ws, "d1") == retry

    def test_update_refuses_bad_values_and_unknown_jobs(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        for bad in (
            {"state": "sleeping"},
            {"done": -1},
            {"total": -1},
            {"attempt": -1},
            {"step": ""},  # an empty string is never a quiet way to clear
            {"error_code": ""},
            {"expected_state": "sleeping"},
        ):
            with pytest.raises(ValueError):
                imports.update(ws, "d1", **bad)
        assert imports.get_for_processing(ws, "d1").state == "queued"
        with pytest.raises(NotFoundError):
            imports.update(ws, "unknown", state="ready")
        with pytest.raises(NotFoundError):
            imports.get_for_processing(ws, "unknown")

    def test_cancel_request_is_visible_to_the_worker(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        imports.update(ws, "d1", state="parsing")
        assert imports.is_cancel_requested(ws, "d1") is False
        requested = imports.request_cancel(ws, OWNER, "d1")
        assert requested.cancel_requested is True and requested.state == "parsing"
        assert imports.is_cancel_requested(ws, "d1") is True
        assert imports.get_for_processing(ws, "d1").cancel_requested is True
        imports.request_cancel(ws, OWNER, "d1")  # asking twice is fine
        assert imports.is_cancel_requested(ws, "unknown") is False

    def test_delete_removes_the_job_and_is_repeatable(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        sources.add(ws, source("d2", ws=ws))
        imports.create(ws, OWNER, "d1")
        imports.create(ws, OWNER, "d2")
        imports.delete(ws, "d1")
        imports.delete(ws, "d1")
        with pytest.raises(NotFoundError):
            imports.get_owned(ws, OWNER, "d1")
        assert imports.get_owned(ws, OWNER, "d2").source_id == "d2"
        # the source can start over after its job was deleted
        assert imports.create(ws, OWNER, "d1").state == "queued"

    def test_workspace_isolation(self, sources, imports, ws) -> None:
        other = f"{ws}-other"
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        imports.update(ws, "d1", state="parsing")
        imports.request_cancel(ws, OWNER, "d1")

        with pytest.raises(NotFoundError):
            imports.get_owned(other, OWNER, "d1")
        with pytest.raises(NotFoundError):
            imports.get_for_processing(other, "d1")
        with pytest.raises(NotFoundError):
            imports.update(other, "d1", state="ready")
        with pytest.raises(NotFoundError):
            imports.create(other, OWNER, "d1")
        assert imports.is_cancel_requested(other, "d1") is False
        imports.delete(other, "d1")  # a delete elsewhere touches nothing here
        assert imports.get_for_processing(ws, "d1").state == "parsing"
        # the same source id can have its own job in another workspace
        sources.add(other, source("d1", ws=other))
        assert imports.create(other, OWNER, "d1").state == "queued"
        assert imports.is_cancel_requested(other, "d1") is False
        assert imports.get_for_processing(ws, "d1").state == "parsing"

    def test_source_kind_does_not_matter_but_meeting_does(self, sources, imports, ws) -> None:
        for n, kind in enumerate((SourceKind.DECK, SourceKind.AUDIO, SourceKind.NOTE)):
            sources.add(ws, source(f"s{n}", kind, ws=ws))
            assert imports.create(ws, OWNER, f"s{n}").state == "queued"

    def test_retry_reset_clears_the_cancel_flag(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        imports.request_cancel(ws, OWNER, "d1")
        imports.update(ws, "d1", state="cancelled")
        left = imports.update(ws, "d1", state="queued", step=CLEAR, error_code=CLEAR)
        assert left.cancel_requested is True  # the flag only moves when it is asked to
        retry = imports.update(ws, "d1", cancel_requested=False)
        assert retry.cancel_requested is False and retry.state == "queued"
        assert imports.is_cancel_requested(ws, "d1") is False
        imports.update(ws, "d1", cancel_requested=True)
        assert imports.is_cancel_requested(ws, "d1") is True

    def test_a_late_worker_write_does_not_overwrite_cancelled_or_failed(
        self, sources, imports, ws
    ) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        imports.update(ws, "d1", state="parsing", done=1, total=3)
        imports.update(ws, "d1", state="cancelled", step="stopped")
        # the worker was in the middle of a stage and still believes the job is parsing
        with pytest.raises(ConflictError):
            imports.update(ws, "d1", state="indexing", done=2, expected_state="parsing")
        with pytest.raises(ConflictError):
            imports.update(ws, "d1", state="ready", expected_state={"parsing", "indexing"})
        held = imports.get_for_processing(ws, "d1")
        assert (held.state, held.step, held.done, held.total) == ("cancelled", "stopped", 1, 3)

        imports.update(ws, "d1", state="failed", error_code="encrypted")
        with pytest.raises(ConflictError):
            imports.update(ws, "d1", state="ready", error_code=CLEAR, expected_state=["parsing"])
        failed = imports.get_for_processing(ws, "d1")
        assert (failed.state, failed.error_code) == ("failed", "encrypted")

    def test_expected_state_lets_a_matching_write_through(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        moved = imports.update(ws, "d1", state="parsing", expected_state="queued")
        assert moved.state == "parsing"
        moved = imports.update(ws, "d1", state="indexing", expected_state={"queued", "parsing"})
        assert moved.state == "indexing"
        # without expected_state the write is unconditional, as before
        assert imports.update(ws, "d1", state="queued").state == "queued"
        with pytest.raises(NotFoundError):
            imports.update(ws, "unknown", state="ready", expected_state="queued")

    def test_concurrent_cas_leaves_one_winner(self, sources, imports, ws) -> None:
        sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        outcomes = _race(
            [
                lambda: imports.update(ws, "d1", state="parsing", expected_state="queued"),
                lambda: imports.update(ws, "d1", state="cancelled", expected_state="queued"),
                lambda: imports.update(ws, "d1", state="failed", expected_state="queued"),
            ]
        )
        wins = [o for o in outcomes if not isinstance(o, BaseException)]
        assert len(wins) == 1, outcomes
        assert all(isinstance(o, ConflictError) for o in outcomes if o not in wins)
        assert imports.get_for_processing(ws, "d1").state == wins[0].state

    def test_create_keeps_the_job_of_a_source_deleted_meanwhile(self, sources, imports, ws) -> None:
        added = sources.add(ws, source("d1", ws=ws))
        imports.create(ws, OWNER, "d1")
        sources.save(ws, replace(added, tombstoned_at=T0))
        # the service deletes the job when it deletes the source; until then it is still there
        assert imports.create(ws, OWNER, "d1").state == "queued"
