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
from nodo_api.services.vault_export import VaultExportService


def test_vault_export_skips_pending_pages():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    store._pages[("ws", "pending")] = Page(
        "pending", "ws", "owner", PageKind.CONCEPT, "concept", PageStatus.PROPOSED, now
    )

    assert VaultExportService(store).render("ws", "owner") == {}


def test_vault_export_never_keeps_text_from_an_erased_source():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page("p", "ws", "owner", PageKind.CONCEPT, "c", PageStatus.PROPOSED, now)
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 6)), "Secret")
    claim = PageClaim(0, (0, 6), "Secret", claim_text_hash("Secret"), (citation,), True)
    store.put_proposal(
        page, PageProposal("pp", "p", "Secret", (claim,), PageProvenance("m", "p"), now)
    )
    store.append_revision("ws", "owner", PageRevision("p", 1, "Secret", "owner", now, (claim,)))

    store.redact_source("ws", "source")

    rendered = "".join(VaultExportService(store).render("ws", "owner").values())
    assert "Secret" not in rendered and "fragment" not in rendered
