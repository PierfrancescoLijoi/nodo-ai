"""Wiki proposal validation and revision commands (ADR-031)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from nodo_api.domain.models import Answerability, Citation
from nodo_api.domain.pages import (
    Page,
    PageCitation,
    PageClaim,
    PageProposal,
    PageProvenance,
    PageRevision,
    claim_text_hash,
)
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

    def compile(
        self,
        page: Page,
        evidence: tuple[Fragment, ...],
        *,
        model_version: str,
        prompt_version: str,
    ) -> PageProposal:
        """Create a conservative cited draft from authorized evidence."""
        if not evidence:
            raise ValidationFailed("A Page compiler needs evidence")
        parts, claims = [], []
        offset = 0
        for ordinal, fragment in enumerate(evidence):
            text = fragment.text
            if parts:
                offset += 2
            parts.append(text)
            claims.append(
                PageClaim(
                    ordinal,
                    (offset, offset + len(text)),
                    text,
                    claim_text_hash(text),
                    (
                        PageCitation(
                            fragment.source_id,
                            fragment.id,
                            fragment.version,
                            fragment.locator,
                            text,
                        ),
                    ),
                    machine_generated=True,
                )
            )
            offset += len(text)
        proposal = PageProposal(
            f"page-proposal-{uuid4().hex}",
            page.id,
            "\n\n".join(parts),
            tuple(claims),
            PageProvenance(model_version, prompt_version),
            datetime.now(UTC),
        )
        self.propose(page, proposal, evidence)
        return proposal

    def approve(self, workspace_id: str, owner_id: str, revision: PageRevision) -> Page:
        return self._pages.append_revision(workspace_id, owner_id, revision)

    def edit(
        self,
        workspace_id: str,
        owner_id: str,
        page_id: str,
        body_md: str,
        claims: tuple[PageClaim, ...],
        evidence: tuple[Fragment, ...],
    ) -> Page:
        page = self._pages.get_owned(workspace_id, owner_id, page_id)
        validated = tuple(
            self._validated_claim(workspace_id, claim, evidence) if claim.citations else claim
            for claim in claims
        )
        current = self._pages.revisions(workspace_id, owner_id, page_id)
        if current and current[0].body_md == body_md and current[0].claims == validated:
            return page
        revision = PageRevision(
            page_id,
            (page.current_revision or 0) + 1,
            body_md,
            owner_id,
            datetime.now(UTC),
            validated,
        )
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

    def reject_proposal(self, workspace_id: str, owner_id: str, page_id: str) -> Page:
        return self._pages.reject_proposal(workspace_id, owner_id, page_id)

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
