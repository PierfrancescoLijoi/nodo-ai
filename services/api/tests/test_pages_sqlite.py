from datetime import UTC, datetime

from nodo_api.adapters.local.db import LocalDatabase
from nodo_api.adapters.local.pages import SqlitePageRepository
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


def test_sqlite_page_revision_and_redaction_round_trip(tmp_path):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    db = LocalDatabase(tmp_path / "pages.db")
    try:
        store = SqlitePageRepository(db)
        page = Page("page", "ws", "owner", PageKind.CONCEPT, "concept", PageStatus.PROPOSED, now)
        citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 5)), "Alpha")
        claim = PageClaim(0, (0, 5), "Alpha", claim_text_hash("Alpha"), (citation,), True)
        proposal = PageProposal(
            "proposal", page.id, "Alpha", (claim,), PageProvenance("model", "prompt"), now
        )
        store.put_proposal(page, proposal)

        approved = store.append_revision(
            "ws", "owner", PageRevision(page.id, 1, "Alpha", "owner", now, (claim,))
        )
        assert approved.status is PageStatus.APPROVED
        assert store.revisions("ws", "owner", page.id)[0].claims == (claim,)

        assert store.redact_source("ws", "source") == (page.id,)
        assert store.revisions("ws", "owner", page.id)[0].body_md == "removed: source deleted"
    finally:
        db.close()


def test_sqlite_refresh_proposal_keeps_the_existing_revision(tmp_path):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    db = LocalDatabase(tmp_path / "refresh.db")
    try:
        store = SqlitePageRepository(db)
        page = Page("p", "ws", "owner", PageKind.CONCEPT, "concept", PageStatus.PROPOSED, now)
        citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 5)), "Alpha")
        first = PageClaim(0, (0, 5), "Alpha", claim_text_hash("Alpha"), (citation,), True)
        store.put_proposal(
            page, PageProposal("first", page.id, "Alpha", (first,), PageProvenance("m", "p"), now)
        )
        approved = store.append_revision(
            "ws", "owner", PageRevision(page.id, 1, "Alpha", "owner", now, (first,))
        )
        second = PageClaim(0, (0, 4), "Beta", claim_text_hash("Beta"), (citation,), True)
        store.put_proposal(
            approved,
            PageProposal("refresh", page.id, "Beta", (second,), PageProvenance("m", "p"), now),
        )

        assert store.revisions("ws", "owner", page.id)[0].body_md == "Alpha"
        assert store.proposal("ws", "owner", page.id).body_md == "Beta"
    finally:
        db.close()


def test_sqlite_rejection_keeps_a_proposal_and_survives_restart(tmp_path):
    now = datetime(2026, 11, 24, tzinfo=UTC)
    path = tmp_path / "rejected.db"
    db = LocalDatabase(path)
    try:
        store = SqlitePageRepository(db)
        page = Page("page", "ws", "owner", PageKind.CONCEPT, "concept", PageStatus.PROPOSED, now)
        citation = PageCitation("source", "fragment", 1, Locator(chars=(0, 5)), "Alpha")
        first = PageClaim(0, (0, 5), "Alpha", claim_text_hash("Alpha"), (citation,), True)
        store.put_proposal(
            page,
            PageProposal("proposal", page.id, "Alpha", (first,), PageProvenance("m", "p"), now),
        )
        assert store.reject_proposal("ws", "owner", page.id).status is PageStatus.REJECTED
    finally:
        db.close()

    reopened = LocalDatabase(path)
    try:
        store = SqlitePageRepository(reopened)
        assert store.get_owned("ws", "owner", "page").status is PageStatus.REJECTED
        assert store.proposal("ws", "owner", "page").id == "proposal"
    finally:
        reopened.close()
