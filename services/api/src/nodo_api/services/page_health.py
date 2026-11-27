"""Deterministic Page health checks (FR-16)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nodo_api.domain.pages import PageStatus
from nodo_api.ports import FragmentRepository, PageRepository


class PageFindingKind(StrEnum):
    STALE_PAGE = "stale_page"
    UNCITED_HUMAN_CLAIM = "uncited_human_claim"
    DANGLING_EVIDENCE = "dangling_evidence"


@dataclass(frozen=True, slots=True)
class PageFinding:
    kind: PageFindingKind
    page_id: str
    revision: int | None = None
    claim_ordinal: int | None = None


class PageHealthService:
    def __init__(self, pages: PageRepository, fragments: FragmentRepository | None = None) -> None:
        self._pages = pages
        self._fragments = fragments

    def verify(self, workspace_id: str, owner_id: str) -> tuple[PageFinding, ...]:
        findings: list[PageFinding] = []
        for page in self._pages.list_owned(workspace_id, owner_id):
            if page.status is PageStatus.STALE:
                findings.append(PageFinding(PageFindingKind.STALE_PAGE, page.id))
            for revision in self._pages.revisions(workspace_id, owner_id, page.id):
                findings.extend(
                    PageFinding(
                        PageFindingKind.UNCITED_HUMAN_CLAIM,
                        page.id,
                        revision.number,
                        claim.ordinal,
                    )
                    for claim in revision.claims
                    if not claim.machine_generated and not claim.citations
                )
                if self._fragments is not None:
                    findings.extend(
                        PageFinding(
                            PageFindingKind.DANGLING_EVIDENCE,
                            page.id,
                            revision.number,
                            claim.ordinal,
                        )
                        for claim in revision.claims
                        if any(
                            not self._citation_is_current(workspace_id, citation)
                            for citation in claim.citations
                        )
                    )
        return tuple(findings)

    def _citation_is_current(self, workspace_id: str, citation) -> bool:
        assert self._fragments is not None
        fragment = self._fragments.get_fragment(
            workspace_id, citation.source_id, citation.fragment_id
        )
        return (
            fragment is not None
            and fragment.version == citation.source_version
            and fragment.locator == citation.locator
            and citation.quote in fragment.text
        )
