from __future__ import annotations

from nodo_api.domain.answer_cache import citations_from_json, citations_to_json
from nodo_api.domain.models import Answerability, Citation
from nodo_api.domain.sources import Fragment, Locator
from nodo_api.services.query import EvidenceValidator, GeneratedAnswer

WORKSPACE = "workspace-a"
SOURCE = "source-a"
FRAGMENT = "fragment-a"


class FragmentLookup:
    def __init__(self, fragment: Fragment | None) -> None:
        self.fragment = fragment

    def get_fragment(self, workspace_id: str, source_id: str, fragment_id: str):
        if (workspace_id, source_id, fragment_id) == (WORKSPACE, SOURCE, FRAGMENT):
            return self.fragment
        return None


class NoTranscripts:
    def get_segment(self, workspace_id: str, meeting_id: str, segment_id: str):
        return None


def evidence() -> Fragment:
    return Fragment(
        id=FRAGMENT,
        source_id=SOURCE,
        section_id="section-a",
        ordinal=0,
        text="The migration uses a bounded retry queue.",
        locator=Locator(page=4, bbox=(0.1, 0.2, 0.8, 0.4)),
        version=3,
    )


def citation(**changes) -> Citation:
    values = {
        "quote": "bounded retry queue",
        "source_id": SOURCE,
        "fragment_id": FRAGMENT,
        "source_version": 3,
        "locator": evidence().locator,
    }
    values.update(changes)
    return Citation(**values)


def validate(item: Citation, fragment: Fragment | None = None, *, owns=True):
    stored = evidence() if fragment is None else fragment
    validator = EvidenceValidator(NoTranscripts(), FragmentLookup(stored))
    generated = GeneratedAnswer("Grounded", Answerability.SUPPORTED, (item,), ())
    return validator.validate(
        WORKSPACE,
        generated,
        (evidence(),),
        owns=(lambda source_id: source_id == SOURCE) if owns else (lambda source_id: False),
    )


def test_document_citation_accepts_a_substring_and_keeps_its_locator() -> None:
    result = validate(citation())

    assert result.answerability is Answerability.SUPPORTED
    assert result.citations == (citation(),)


def test_document_citation_rejects_fabricated_or_out_of_scope_evidence() -> None:
    assert (
        validate(citation(quote="not in the fragment")).answerability is Answerability.UNANSWERABLE
    )
    assert validate(citation(), owns=False).answerability is Answerability.UNANSWERABLE


def test_document_citation_rejects_a_stale_version_or_changed_locator() -> None:
    assert validate(citation(source_version=2)).answerability is Answerability.UNANSWERABLE
    assert validate(citation(locator=Locator(page=5))).answerability is Answerability.UNANSWERABLE


def test_document_citation_requires_complete_inspectable_provenance() -> None:
    assert validate(citation(quote="")).answerability is Answerability.UNANSWERABLE
    assert validate(citation(quote="   ")).answerability is Answerability.UNANSWERABLE
    assert validate(citation(source_version=None)).answerability is Answerability.UNANSWERABLE
    assert validate(citation(locator=None)).answerability is Answerability.UNANSWERABLE


def test_document_citation_rejects_missing_or_deleted_evidence() -> None:
    validator = EvidenceValidator(NoTranscripts(), FragmentLookup(None))
    generated = GeneratedAnswer("Grounded", Answerability.SUPPORTED, (citation(),), ())

    result = validator.validate(WORKSPACE, generated, (evidence(),), owns=lambda _: True)

    assert result == GeneratedAnswer("", Answerability.UNANSWERABLE, (), ())


def test_source_neutral_cache_json_round_trips_and_legacy_json_still_loads() -> None:
    source_item = citation()
    assert citations_from_json(citations_to_json((source_item,))) == (source_item,)

    legacy = citations_from_json(
        [
            {
                "meeting_id": "meeting-a",
                "segment_id": "segment-a",
                "start_ms": 100,
                "end_ms": 900,
                "quote": "A legacy citation.",
            }
        ]
    )
    assert legacy == (Citation("meeting-a", "segment-a", 100, 900, "A legacy citation."),)
