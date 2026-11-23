"""Safe, version-pinned summaries for expanding retrieval context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, SectionSummary, estimate_tokens
from nodo_api.errors import NotFoundError
from nodo_api.ports import SectionSummaryRepository
from nodo_api.resilience import DependencyUnavailable, Overloaded

PROMPT_VERSION = "section-summary-v1"
MAX_SUMMARY_CHARS = 2_000


class SectionSummarizer(Protocol):
    model_version: str

    def summarize(self, locale: Locale, fragments: tuple[Fragment, ...]) -> str: ...


class SectionSummaryService:
    def __init__(self, summaries: SectionSummaryRepository, summarizer: SectionSummarizer) -> None:
        self._summaries = summaries
        self._summarizer = summarizer

    def get_or_generate(
        self,
        workspace_id: str,
        section_id: str,
        fragments: Sequence[Fragment],
        locale: Locale,
    ) -> SectionSummary | None:
        """Best effort only: an unavailable model never makes an answer unavailable."""
        ordered = tuple(
            sorted(
                (f for f in fragments if f.section_id == section_id),
                key=lambda f: (f.ordinal, f.id),
            )
        )
        if not ordered:
            return None
        source_id, version = ordered[0].source_id, ordered[0].version
        if any((f.source_id, f.version) != (source_id, version) for f in ordered):
            raise ValueError("A section summary needs fragments from one source version")
        cached = self._summaries.get(workspace_id, source_id, section_id, version)
        if cached is not None:
            return cached
        try:
            text = self._summarizer.summarize(locale, ordered).strip()[:MAX_SUMMARY_CHARS]
        except (DependencyUnavailable, Overloaded):
            return None
        if not text:
            return None
        summary = SectionSummary(
            source_id,
            section_id,
            version,
            self._summarizer.model_version,
            PROMPT_VERSION,
            text,
            max(1, estimate_tokens(text)),
        )
        try:
            self._summaries.put(workspace_id, summary)
        except NotFoundError:
            return None  # replacement or deletion won the race
        return summary
