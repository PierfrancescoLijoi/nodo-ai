from datetime import UTC, datetime

import pytest

from nodo_api.adapters.memory.adapters import MemoryMeetingRepository, MemoryTranscriptRepository
from nodo_api.adapters.memory.pages import MemoryPageRepository
from nodo_api.adapters.memory.sources import MemoryFragmentRepository, MemorySourceRepository
from nodo_api.domain.pages import (
    Page,
    PageCitation,
    PageClaim,
    PageKind,
    PageProposal,
    PageProvenance,
    PageRevision,
    PageStatus,
    claim_text_hash,
)
from nodo_api.domain.sources import Fragment, Locator
from nodo_api.errors import ValidationFailed
from nodo_api.services.pages import PageService
from nodo_api.services.query import EvidenceValidator


class _AcceptingValidator:
    def validate(self, workspace_id, generated, evidence):
        return generated


def test_proposal_rejects_a_quote_that_is_not_in_its_fragment():
    meetings, transcripts = MemoryMeetingRepository(), MemoryTranscriptRepository()
    fragments = MemoryFragmentRepository(MemorySourceRepository(meetings), transcripts)
    service = PageService(MemoryPageRepository(), EvidenceValidator(transcripts, fragments))
    now = datetime(2026, 11, 24, tzinfo=UTC)
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    fragment = Fragment("f", "source", None, 0, "stored fact", Locator(chars=(0, 11)), 1)
    citation = PageCitation("source", "f", 1, fragment.locator, "invented")
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    proposal = PageProposal("pp", "p", "Fact", (claim,), PageProvenance("m", "p"), now)

    with pytest.raises(ValidationFailed):
        service.propose(page, proposal, (fragment,))


def test_approval_turns_the_pending_proposal_into_an_authoritative_revision():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 4)), "Fact")
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    store.put_proposal(
        page, PageProposal("pp", "p", "Fact", (claim,), PageProvenance("m", "p"), now)
    )
    _meetings, transcripts = MemoryMeetingRepository(), MemoryTranscriptRepository()
    service = PageService(store, EvidenceValidator(transcripts))

    approved = service.approve_proposal("ws", "owner", "p")

    assert approved.current_revision == 1
    assert store.revisions("ws", "owner", "p")[0].body_md == "Fact"


def test_human_edit_appends_a_revision_without_overwriting_history():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 4)), "Fact")
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    store.put_proposal(
        page, PageProposal("pp", "p", "Fact", (claim,), PageProvenance("m", "p"), now)
    )
    store.append_revision("ws", "owner", PageRevision("p", 1, "Fact", "owner", now, (claim,)))
    _meetings, transcripts = MemoryMeetingRepository(), MemoryTranscriptRepository()
    service = PageService(store, EvidenceValidator(transcripts))
    human = PageClaim(0, (0, 4), "Note", claim_text_hash("Note"), ())

    edited = service.edit("ws", "owner", "p", "Note", (human,), ())

    assert edited.current_revision == 2
    assert [item.body_md for item in store.revisions("ws", "owner", "p")] == ["Note", "Fact"]


def test_compiler_creates_a_cited_proposal_from_authorized_evidence():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    fragment = Fragment("f", "source", None, 0, "Stored fact", Locator(chars=(0, 11)), 1)
    service = PageService(store, _AcceptingValidator())

    proposal = service.compile(
        page, (fragment,), model_version="extractive-v1", prompt_version="page-v1"
    )

    assert proposal.body_md == "Stored fact"
    assert proposal.claims[0].citations[0].fragment_id == "f"
    assert store.proposal("ws", "owner", "p") == proposal
