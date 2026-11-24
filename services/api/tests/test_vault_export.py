from datetime import UTC, datetime
from pathlib import Path

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

    rendered = VaultExportService(store).render("ws", "owner")
    assert set(rendered) == {Path("manifest.json")}


def test_vault_export_never_keeps_text_from_an_erased_source():
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page("p", "ws", "owner", PageKind.SOURCE_SUMMARY, "source", PageStatus.PROPOSED, now)
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 6)), "Secret")
    claim = PageClaim(0, (0, 6), "Secret", claim_text_hash("Secret"), (citation,), True)
    store.put_proposal(
        page, PageProposal("pp", "p", "Secret", (claim,), PageProvenance("m", "p"), now)
    )
    store.append_revision("ws", "owner", PageRevision("p", 1, "Secret", "owner", now, (claim,)))

    store.redact_source("ws", "source")

    rendered = "".join(VaultExportService(store).render("ws", "owner").values())
    assert "Secret" not in rendered and "fragment" not in rendered


def test_vault_writer_keeps_unmanaged_notes_and_removes_only_old_managed_pages(tmp_path):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    store = MemoryPageRepository()
    page = Page(
        "p", "ws", "owner", PageKind.SOURCE_SUMMARY, "source", PageStatus.PROPOSED, now
    )
    citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 4)), "Fact")
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation,), True)
    store.put_proposal(
        page, PageProposal("pp", "p", "Fact", (claim,), PageProvenance("m", "p"), now)
    )
    store.append_revision("ws", "owner", PageRevision("p", 1, "Fact", "owner", now, (claim,)))
    writer = VaultExportService(store)
    note = tmp_path / "my-note.md"
    note.write_text("mine", encoding="utf-8")

    writer.write("ws", "owner", tmp_path)
    store.redact_source("ws", "source")
    writer.write("ws", "owner", tmp_path)

    assert note.read_text(encoding="utf-8") == "mine"
    assert not (tmp_path / "pages" / "p.md").exists()
    assert (tmp_path / "manifest.json").is_file()
