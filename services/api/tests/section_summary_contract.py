"""The version and deletion rules shared by memory and SQLite section summaries."""

from __future__ import annotations

import pytest

from nodo_api.domain.sources import SectionSummary
from nodo_api.errors import NotFoundError
from tests.source_contract import fragment, section, source


class SectionSummaryContract:
    def _seed(self, sources, fragments, ws):
        sources.add(ws, source("doc", ws=ws))
        fragments.add_version(
            ws, "doc", (section("s", "doc", 0),), (fragment("f", "doc", 0, section="s"),)
        )

    def test_summary_is_exactly_version_pinned(self, summaries, sources, fragments, ws):
        self._seed(sources, fragments, ws)
        summary = SectionSummary("doc", "s", 1, "model", "prompt", "summary", 1)
        summaries.put(ws, summary)
        assert summaries.get(ws, "doc", "s", 1) == summary

        fragments.add_version(
            ws, "doc", (section("s", "doc", 0),), (fragment("f2", "doc", 0, section="s"),)
        )
        assert summaries.get(ws, "doc", "s", 1) is None
        with pytest.raises(NotFoundError):
            summaries.put(ws, summary)

    def test_summary_disappears_with_source(self, summaries, sources, fragments, ws):
        self._seed(sources, fragments, ws)
        summaries.put(ws, SectionSummary("doc", "s", 1, "model", "prompt", "summary", 1))
        fragments.erase(ws, "doc")
        summaries.erase_source(ws, "doc")
        assert summaries.get(ws, "doc", "s", 1) is None
