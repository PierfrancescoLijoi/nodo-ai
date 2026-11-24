from datetime import UTC, datetime

from nodo_api.adapters.memory.pages import MemoryPageRepository
from nodo_api.domain.pages import Page, PageKind, PageStatus
from nodo_api.services.vault_export import VaultExportService


def test_vault_export_skips_pending_pages():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    store._pages[("ws", "pending")] = Page(
        "pending", "ws", "owner", PageKind.CONCEPT, "concept", PageStatus.PROPOSED, now
    )

    assert VaultExportService(store).render("ws", "owner") == {}
