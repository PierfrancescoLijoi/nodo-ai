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
    PageStatus,
    claim_text_hash,
)
from nodo_api.domain.sources import Fragment, Locator
from nodo_api.errors import ValidationFailed
from nodo_api.services.pages import PageService
from nodo_api.services.query import EvidenceValidator


def test_proposal_rejects_a_quote_that_is_not_in_its_fragment():
    meetings, transcripts = MemoryMeetingRepository(), MemoryTranscriptRepository()
    fragments = MemoryFragmentRepository(MemorySourceRepository(meetings), transcripts)
    service = PageService(MemoryPageRepository(), EvidenceValidator(transcripts, fragments))
    now = datetime(2026, 11, 24, tzinfo=UTC)
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    fragment = Fragment("f", "source", None, 0, "stored fact", Locator(chars=(0, 11)), 1)
    citation = PageCitation("source", "f", 1, fragment.locator, "invented")
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    proposal = PageProposal("pp", "p", (claim,), PageProvenance("m", "p"), now)

    with pytest.raises(ValidationFailed):
        service.propose(page, proposal, (fragment,))
