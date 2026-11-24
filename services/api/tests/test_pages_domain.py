from datetime import UTC, datetime

import pytest

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

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def citation() -> PageCitation:
    return PageCitation("source-1", "fragment-1", 2, Locator(chars=(0, 12)), "supported text")


def test_machine_claim_needs_a_citation_and_a_valid_span():
    with pytest.raises(ValueError, match="machine-generated claim needs a citation"):
        PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (), machine_generated=True)
    with pytest.raises(ValueError, match="non-negative range"):
        PageClaim(0, (-1, 4), "Fact", claim_text_hash("Fact"), (citation(),))


def test_human_claim_can_be_uncited_for_health_lint_to_report():
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (), machine_generated=False)

    assert claim.citations == ()


def test_page_revision_is_append_only_numbered_and_has_claims_in_body_order():
    claim = PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation(),))
    revision = PageRevision("page-1", 1, "Fact", "user-1", NOW, (claim,))

    assert revision.number == 1
    with pytest.raises(ValueError, match="number starts at 1"):
        PageRevision("page-1", 0, "Fact", "user-1", NOW, (claim,))


def test_page_can_keep_an_approved_revision_while_a_refresh_proposal_exists():
    page = Page(
        "page-1",
        "workspace-1",
        "owner-1",
        PageKind.CONCEPT,
        "concept-1",
        PageStatus.STALE,
        NOW,
        current_revision=2,
    )

    assert page.current_revision == 2
    proposal = PageProposal(
        "proposal-1",
        page.id,
        (PageClaim(0, (0, 4), "Fact", claim_text_hash("Fact"), (citation(),), True),),
        PageProvenance("model", "prompt"),
        NOW,
    )

    assert proposal.page_id == page.id
    with pytest.raises(ValueError, match="needs a claim"):
        PageProposal("proposal-2", page.id, (), PageProvenance("model", "prompt"), NOW)


def test_new_page_is_proposed_before_its_first_approved_revision():
    page = Page(
        "page-2",
        "workspace-1",
        "owner-1",
        PageKind.CONCEPT,
        "concept-2",
        PageStatus.PROPOSED,
        NOW,
    )

    assert page.current_revision is None


def test_machine_provenance_is_complete_and_human_provenance_is_empty():
    assert PageProvenance("model-v1", "prompt-v1").machine_generated
    assert not PageProvenance().machine_generated
    with pytest.raises(ValueError, match="both model and prompt"):
        PageProvenance("model-v1")
