from datetime import UTC, datetime

from nodo_api.adapters.memory.pages import MemoryPageRepository
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
from nodo_api.domain.sources import Locator
from nodo_api.services.page_health import PageFindingKind, PageHealthService


def test_health_reports_stale_pages_and_uncited_human_claims():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 5)), "Fact")
    machine = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    human = PageClaim(1, (5, 9), "Note", claim_text_hash("Note"), ())
    store = MemoryPageRepository()
    store.put_proposal(
        page, PageProposal("pp", "p", "Fact", (machine,), PageProvenance("m", "p"), now)
    )
    store.append_revision(
        "ws", "owner", PageRevision("p", 1, "Fact Note", "owner", now, (machine, human))
    )
    store.mark_stale_for_source("ws", "source")

    found = PageHealthService(store).verify("ws", "owner")
    assert {item.kind for item in found} == {
        PageFindingKind.STALE_PAGE,
        PageFindingKind.UNCITED_HUMAN_CLAIM,
    }
