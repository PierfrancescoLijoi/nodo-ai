from __future__ import annotations

from dataclasses import replace

from nodo_api.domain.models import Answerability, Citation, Locale, MeetingState
from nodo_api.main import AppContainer
from nodo_api.services.query import EvidenceValidator, GeneratedAnswer, QueryRequest

WORKSPACE = "workspace-a"
USER = "user-a"


def _allowed(container: AppContainer, workspace: str = WORKSPACE, user: str = USER):
    return container.meetings.owned_ids(workspace, user)


def _search(container: AppContainer, query: str, workspace: str = WORKSPACE):
    return container.retriever.search(workspace, query, None, _allowed(container), 20)


def test_create_is_idempotent_and_complete_publishes_once(app, attestation) -> None:
    container: AppContainer = app.state.container
    service = container.meeting_service
    seeded = len(container.meetings.all_for_workspace(WORKSPACE))
    first = service.create(WORKSPACE, USER, "same-key-1", attestation)
    second = service.create(WORKSPACE, USER, "same-key-1", attestation)
    assert first.id == second.id
    assert len(container.meetings.all_for_workspace(WORKSPACE)) == seeded + 1

    completed = service.complete(WORKSPACE, USER, first.id)
    completed_again = service.complete(WORKSPACE, USER, first.id)
    assert completed.state is MeetingState.TRANSCRIBING
    assert completed_again.state is MeetingState.TRANSCRIBING
    assert container.jobs.publish_count(WORKSPACE, first.id, "transcribe") == 1


def test_review_creates_v2_and_keeps_v1(app) -> None:
    container: AppContainer = app.state.container
    original = container.transcripts.get_version(WORKSPACE, "seed-m1", 1)
    revised = container.meeting_service.review(
        WORKSPACE,
        USER,
        "seed-m1",
        [{"segment_id": original[0].id, "text": "Corrected immutable text"}],
    )
    assert revised[0].version == 2
    assert revised[0].text == "Corrected immutable text"
    persisted = container.transcripts.get_version(WORKSPACE, "seed-m1", 1)
    assert persisted[0].text == original[0].text


def test_evidence_validator_drops_fabricated_citation(app) -> None:
    container: AppContainer = app.state.container
    retrieved = _search(container, "async queue")
    fabricated = Citation("seed-m1", "made-up", 0, 1, "fiction")
    generated = GeneratedAnswer("Unsupported", Answerability.SUPPORTED, (fabricated,), ())
    validated = EvidenceValidator(container.transcripts).validate(WORKSPACE, generated, retrieved)
    assert validated.answerability is Answerability.UNANSWERABLE
    assert validated.citations == ()


def test_evidence_validator_requires_accessible_segment(app) -> None:
    container: AppContainer = app.state.container
    segment = _search(container, "async")[0]
    citation = Citation(segment.meeting_id, segment.id, 0, 1, segment.text)
    generated = GeneratedAnswer("Grounded", Answerability.SUPPORTED, (citation,), ())
    assert (
        EvidenceValidator(container.transcripts)
        .validate("workspace-b", generated, (segment,))
        .answerability
        is Answerability.UNANSWERABLE
    )


def test_evidence_validator_rejects_fabricated_quote(app) -> None:
    container: AppContainer = app.state.container
    segment = _search(container, "async")[0]
    citation = Citation(segment.meeting_id, segment.id, segment.start_ms, segment.end_ms, "Fake")
    generated = GeneratedAnswer("Grounded", Answerability.SUPPORTED, (citation,), ())
    validated = EvidenceValidator(container.transcripts).validate(WORKSPACE, generated, (segment,))
    assert validated.answerability is Answerability.UNANSWERABLE
    assert validated.citations == ()


def test_delete_tombstones_and_erases_derived_data(app) -> None:
    container: AppContainer = app.state.container
    assert _search(container, "synchronous")
    deleted = container.meeting_service.delete(WORKSPACE, USER, "seed-m1")
    assert deleted.tombstoned_at is not None
    assert not any(segment.meeting_id == "seed-m1" for segment in _search(container, "synchronous"))
    everyone = frozenset({"seed-m1", "seed-m2", "seed-m3"})
    assert container.graph.lookup(WORKSPACE, "ingestion", 2, 50, everyone) == ()
    assert container.blobs.was_erased(WORKSPACE, "seed-m1")


def test_query_enforces_graph_bounds_and_localizes(app) -> None:
    container: AppContainer = app.state.container
    for locale in Locale:
        result = container.query_service.query(
            WORKSPACE, USER, QueryRequest("What changed in the ingestion pipeline?", locale)
        )
        assert result.answerability is Answerability.SUPPORTED
        assert result.citations
        assert result.answer == container.llm.localized_answer(locale)
    assert container.graph.last_depth == 2
    assert container.graph.last_node_limit == container.settings.graph_node_limit


def test_query_returns_no_citations_without_evidence(app) -> None:
    container: AppContainer = app.state.container
    result = container.query_service.query(
        WORKSPACE, USER, QueryRequest("What is the office Wi-Fi password?", Locale.EN)
    )
    assert result.answerability is Answerability.UNANSWERABLE
    assert result.citations == ()


def test_workspace_b_never_receives_workspace_a_data(app) -> None:
    container: AppContainer = app.state.container
    result = container.query_service.query(
        "workspace-b", "user-b", QueryRequest("ingestion pipeline", Locale.EN)
    )
    assert result.answerability is Answerability.UNANSWERABLE
    assert result.citations == ()
    assert result.related_entities == ()
    assert _search(container, "ingestion", "workspace-b") == ()


def test_repository_returns_new_meeting_on_tombstone(app) -> None:
    container: AppContainer = app.state.container
    before = container.meetings.get_owned(WORKSPACE, USER, "seed-m2")
    container.meeting_service.delete(WORKSPACE, USER, "seed-m2")
    after = container.meetings.get_owned(WORKSPACE, USER, "seed-m2", include_tombstoned=True)
    assert before is not after
    assert replace(after, tombstoned_at=None).id == before.id
