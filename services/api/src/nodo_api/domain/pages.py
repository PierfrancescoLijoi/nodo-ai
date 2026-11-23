"""Cited Wiki page value objects (ADR-031).

Pages are deliberately modelled separately from retrieval answers.  A Page proposal is derived
text; an approved or human-edited revision is authoritative user data.  The persistence and
redaction services added in Phase 5 enforce that distinction, while these values make invalid
states unrepresentable at the domain boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nodo_api.domain.sources import Locator


class PageKind(StrEnum):
    CONCEPT = "concept"
    SOURCE_SUMMARY = "source_summary"
    SAVED_ANSWER = "saved_answer"


class PageStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"


def _range(name: str, value: tuple[int, int]) -> None:
    start, end = value
    if start < 0 or end < start:
        raise ValueError(f"{name} must be a non-negative range with end >= start")


@dataclass(frozen=True, slots=True)
class PageCitation:
    """A source-neutral, version-pinned quote supporting one Page claim."""

    source_id: str
    fragment_id: str
    source_version: int
    locator: Locator
    quote: str

    def __post_init__(self) -> None:
        if not self.source_id or not self.fragment_id or not self.quote.strip():
            raise ValueError("A Page citation needs source, fragment and quote")
        if self.source_version < 1:
            raise ValueError("A Page citation version starts at 1")


@dataclass(frozen=True, slots=True)
class PageClaim:
    """One body passage plus its evidence, stored for both current and historic revisions."""

    ordinal: int
    span: tuple[int, int]
    text: str
    text_hash: str
    citations: tuple[PageCitation, ...]
    machine_generated: bool = False

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("A Page claim ordinal cannot be negative")
        _range("A Page claim span", self.span)
        if not self.text.strip() or not self.text_hash:
            raise ValueError("A Page claim needs text and text hash")
        if self.machine_generated and not self.citations:
            raise ValueError("A machine-generated claim needs a citation")


@dataclass(frozen=True, slots=True)
class PageProvenance:
    """Records a model proposal's origin; human prose intentionally has no model provenance."""

    model_version: str | None = None
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        if bool(self.model_version) != bool(self.prompt_version):
            raise ValueError("Page provenance needs both model and prompt version")

    @property
    def machine_generated(self) -> bool:
        return self.model_version is not None


@dataclass(frozen=True, slots=True)
class PageRevision:
    """An approved or human edit. Redaction is the sole allowed in-place rewrite."""

    page_id: str
    number: int
    body_md: str
    author_id: str
    created_at: datetime
    claims: tuple[PageClaim, ...]
    provenance: PageProvenance = PageProvenance()

    def __post_init__(self) -> None:
        if not self.page_id or not self.author_id or not self.body_md.strip():
            raise ValueError("A Page revision needs page, author and body")
        if self.number < 1:
            raise ValueError("A Page revision number starts at 1")
        ordinals = tuple(claim.ordinal for claim in self.claims)
        if ordinals != tuple(range(len(self.claims))):
            raise ValueError("Page revision claims must have contiguous body order")


@dataclass(frozen=True, slots=True)
class PageProposal:
    """A replaceable machine draft kept beside, never inside, an authoritative Page revision."""

    id: str
    page_id: str
    claims: tuple[PageClaim, ...]
    provenance: PageProvenance
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.page_id:
            raise ValueError("A Page proposal needs id and page")
        if not self.provenance.machine_generated:
            raise ValueError("A Page proposal needs machine provenance")
        if not self.claims:
            raise ValueError("A Page proposal needs a claim")
        if any(not claim.machine_generated for claim in self.claims):
            raise ValueError("A Page proposal contains only machine-generated claims")


@dataclass(frozen=True, slots=True)
class Page:
    id: str
    workspace_id: str
    owner_id: str
    kind: PageKind
    subject_id: str
    status: PageStatus
    created_at: datetime
    current_revision: int | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.workspace_id, self.owner_id, self.subject_id)):
            raise ValueError("A Page needs id, workspace, owner and subject")
        if self.current_revision is not None and self.current_revision < 1:
            raise ValueError("A Page revision starts at 1")
        if self.status in {PageStatus.APPROVED, PageStatus.STALE} and self.current_revision is None:
            raise ValueError("An approved or stale Page needs a revision")
