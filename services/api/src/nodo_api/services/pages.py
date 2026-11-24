"""Wiki proposal validation and revision commands (ADR-031)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from nodo_api.domain.models import Answerability, Citation
from nodo_api.domain.pages import Page, PageClaim, PageProposal, PageRevision
from nodo_api.domain.sources import Fragment
from nodo_api.errors import ValidationFailed
from nodo_api.ports import PageRepository
from nodo_api.services.query import EvidenceValidator, GeneratedAnswer


class PageService:
    """Stores only proposals whose machine claims are backed by current authorized evidence."""

    def __init__(self, pages: PageRepository, validator: EvidenceValidator) -> None:
        self._pages = pages
        self._validator = validator

    def propose(
        self,
        page: Page,
        proposal: PageProposal,
        evidence: tuple[Fragment, ...],
    ) -> None:
        valid = tuple(
            self._validated_claim(page.workspace_id, claim, evidence) for claim in proposal.claims
        )
        if len(valid) != len(proposal.claims):
            raise ValidationFailed("A Page proposal contains unsupported claims")
        self._pages.put_proposal(page, replace(proposal, claims=valid))

    def approve(self, workspace_id: str, owner_id: str, revision: PageRevision) -> Page:
        return self._pages.append_revision(workspace_id, owner_id, revision)

    def approve_proposal(self, workspace_id: str, owner_id: str, page_id: str) -> Page:
        page = self._pages.get_owned(workspace_id, owner_id, page_id)
        proposal = self._pages.proposal(workspace_id, owner_id, page_id)
        if proposal is None:
            if page.current_revision is not None:
                return page  # idempotent retry after the proposal was promoted
            raise ValidationFailed("There is no pending Page proposal")
        revision = PageRevision(
            page_id,
            (page.current_revision or 0) + 1,
            proposal.body_md,
            owner_id,
            datetime.now(UTC),
            proposal.claims,
            proposal.provenance,
        )
        return self._pages.append_revision(workspace_id, owner_id, revision)

    def _validated_claim(
        self, workspace_id: str, claim: PageClaim, evidence: tuple[Fragment, ...]
    ) -> PageClaim:
        citations = tuple(
            Citation(
                quote=citation.quote,
                source_id=citation.source_id,
                fragment_id=citation.fragment_id,
                source_version=citation.source_version,
                locator=citation.locator,
            )
            for citation in claim.citations
        )
        validated = self._validator.validate(
            workspace_id,
            GeneratedAnswer(claim.text, Answerability.SUPPORTED, citations, ()),
            evidence,
        )
        if len(validated.citations) != len(citations):
            raise ValidationFailed("A Page claim has invalid evidence")
        return claim
