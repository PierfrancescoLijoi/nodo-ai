from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nodo_api.adapters.memory.pages import REMOVED_MARKER, MemoryPageRepository
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
from nodo_api.errors import NotFoundError

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def claim(ordinal: int, span: tuple[int, int], text: str, source_id: str) -> PageClaim:
    citation = PageCitation(source_id, f"{source_id}-fragment", 1, Locator(chars=(0, 1)), text)
    return PageClaim(ordinal, span, text, claim_text_hash(text), (citation,), True)


def page(status: PageStatus = PageStatus.PROPOSED, revision: int | None = None) -> Page:
    return Page("page", "ws", "owner", PageKind.CONCEPT, "concept", status, NOW, revision)


def proposal(value: Page, *claims: PageClaim) -> PageProposal:
    return PageProposal(
        "proposal", value.id, "Alpha", claims, PageProvenance("model", "prompt"), NOW
    )


def test_owner_scoping_and_approval_turn_proposal_into_authoritative_revision():
    store, value = MemoryPageRepository(), page()
    first = claim(0, (0, 5), "Alpha", "source-a")
    store.put_proposal(value, proposal(value, first))

    with pytest.raises(NotFoundError):
        store.get_owned("ws", "other", value.id)

    approved = store.append_revision(
        "ws", "owner", PageRevision(value.id, 1, "Alpha", "owner", NOW, (first,))
    )

    assert approved.status is PageStatus.APPROVED
    assert approved.current_revision == 1
    assert store.proposal("ws", "owner", value.id) is None


def test_source_redaction_removes_dependent_claims_from_every_revision_and_marks_stale():
    store = MemoryPageRepository()
    value = page()
    alpha = claim(0, (0, 5), "Alpha", "source-a")
    beta = claim(1, (6, 10), "Beta", "source-b")
    store.put_proposal(value, proposal(value, alpha, beta))
    store.append_revision(
        "ws", "owner", PageRevision(value.id, 1, "Alpha Beta", "owner", NOW, (alpha, beta))
    )

    assert store.redact_source("ws", "source-a") == (value.id,)
    revision = store.revisions("ws", "owner", value.id)[0]

    assert revision.body_md == f"{REMOVED_MARKER} Beta"
    assert revision.claims[0].text == "Beta"
    assert revision.claims[0].span == (len(REMOVED_MARKER) + 1, len(REMOVED_MARKER) + 5)
    assert store.get_owned("ws", "owner", value.id).status is PageStatus.STALE


def test_invalid_revision_span_fails_closed_to_whole_revision_marker():
    store, value = MemoryPageRepository(), page()
    first = claim(0, (0, 5), "Alpha", "source-a")
    store.put_proposal(value, proposal(value, first))
    broken = replace(first, span=(0, 4))
    store.append_revision(
        "ws", "owner", PageRevision(value.id, 1, "Alpha", "owner", NOW, (broken,))
    )

    store.redact_source("ws", "source-a")

    assert store.revisions("ws", "owner", value.id)[0].body_md == REMOVED_MARKER
