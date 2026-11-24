"""In-memory authoritative Wiki Page store (ADR-031)."""

from __future__ import annotations

from dataclasses import replace

from nodo_api.domain.pages import (
    Page,
    PageClaim,
    PageKind,
    PageProposal,
    PageRevision,
    PageStatus,
    claim_text_hash,
)
from nodo_api.errors import ConflictError, NotFoundError

REMOVED_MARKER = "removed: source deleted"


class MemoryPageRepository:
    def __init__(self) -> None:
        self._pages: dict[tuple[str, str], Page] = {}
        self._proposals: dict[tuple[str, str], PageProposal] = {}
        self._revisions: dict[tuple[str, str], list[PageRevision]] = {}

    def put_proposal(self, page: Page, proposal: PageProposal) -> None:
        if proposal.page_id != page.id:
            raise ValueError("Page proposal belongs to another Page")
        key = page.workspace_id, page.id
        old = self._pages.get(key)
        if old is not None and (old.owner_id != page.owner_id or old.kind != page.kind):
            raise ConflictError
        self._pages[key] = page
        self._proposals[key] = proposal

    def get_owned(self, workspace_id: str, owner_id: str, page_id: str) -> Page:
        page = self._pages.get((workspace_id, page_id))
        if page is None or page.owner_id != owner_id:
            raise NotFoundError
        return page

    def list_owned(self, workspace_id: str, owner_id: str) -> tuple[Page, ...]:
        return tuple(
            sorted(
                (
                    page
                    for (ws, _), page in self._pages.items()
                    if ws == workspace_id and page.owner_id == owner_id
                ),
                key=lambda page: (page.created_at, page.id), reverse=True,
            )
        )

    def proposal(self, workspace_id: str, owner_id: str, page_id: str) -> PageProposal | None:
        self.get_owned(workspace_id, owner_id, page_id)
        return self._proposals.get((workspace_id, page_id))

    def append_revision(self, workspace_id: str, owner_id: str, revision: PageRevision) -> Page:
        page = self.get_owned(workspace_id, owner_id, revision.page_id)
        key = workspace_id, revision.page_id
        revisions = self._revisions.setdefault(key, [])
        if revision.number != len(revisions) + 1:
            raise ConflictError
        revisions.append(revision)
        updated = replace(page, status=PageStatus.APPROVED, current_revision=revision.number)
        self._pages[key] = updated
        self._proposals.pop(key, None)
        return updated

    def revisions(self, workspace_id: str, owner_id: str, page_id: str) -> tuple[PageRevision, ...]:
        self.get_owned(workspace_id, owner_id, page_id)
        return tuple(reversed(self._revisions.get((workspace_id, page_id), ())))

    def mark_stale_for_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        affected = []
        for key, page in list(self._pages.items()):
            if key[0] != workspace_id or page.current_revision is None:
                continue
            current = self._revisions[key][-1]
            if page.status is PageStatus.APPROVED and _cites(current.claims, source_id):
                self._pages[key] = replace(page, status=PageStatus.STALE)
                affected.append(page.id)
        return tuple(affected)

    def redact_source(self, workspace_id: str, source_id: str) -> tuple[str, ...]:
        affected = []
        for key, page in list(self._pages.items()):
            if key[0] != workspace_id:
                continue
            proposal = self._proposals.get(key)
            if proposal is not None and _cites(proposal.claims, source_id):
                self._proposals.pop(key)
            revisions = self._revisions.get(key, [])
            changed = False
            for index, revision in enumerate(revisions):
                if _cites(revision.claims, source_id):
                    revisions[index] = _redact(revision, source_id)
                    changed = True
            if changed:
                self._pages[key] = replace(page, status=PageStatus.STALE)
                affected.append(page.id)
            if page.kind is PageKind.SOURCE_SUMMARY and not revisions and proposal is None:
                self._pages.pop(key)
        return tuple(affected)


def _cites(claims: tuple[PageClaim, ...], source_id: str) -> bool:
    return any(citation.source_id == source_id for claim in claims for citation in claim.citations)


def _redact(revision: PageRevision, source_id: str) -> PageRevision:
    if any(
        revision.body_md[claim.span[0] : claim.span[1]] != claim.text
        or claim_text_hash(claim.text) != claim.text_hash
        for claim in revision.claims
    ):
        return replace(revision, body_md=REMOVED_MARKER, claims=())
    body, shift, claims = revision.body_md, 0, []
    for claim in revision.claims:
        start, end = claim.span[0] + shift, claim.span[1] + shift
        if any(citation.source_id == source_id for citation in claim.citations):
            body = body[:start] + REMOVED_MARKER + body[end:]
            shift += len(REMOVED_MARKER) - (end - start)
        else:
            claims.append(
                replace(
                    claim,
                    ordinal=len(claims),
                    span=(start, end),
                    text_hash=claim_text_hash(claim.text),
                )
            )
    return replace(revision, body_md=body, claims=tuple(claims))
