from __future__ import annotations

from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.retrieval import CitationFormat, QueryScopeMode, ResolvedQueryScope
from nodo_api.domain.sources import Fragment, FragmentHit, Locator
from nodo_api.services.query import QueryRequest, QueryService


class RecordingRetriever:
    def __init__(self, segment: TranscriptSegment) -> None:
        self.segment = segment
        self.scopes: list[frozenset[str] | None] = []

    def search(self, *args, source_ids=None):
        self.scopes.append(source_ids)
        return (self.segment,)


class RecordingFragmentIndex:
    def __init__(self, fragment: Fragment) -> None:
        self.fragment = fragment
        self.scopes: list[frozenset[str] | None] = []

    def search(self, *args, source_ids=None):
        self.scopes.append(source_ids)
        return (FragmentHit(self.fragment, 0.8),)


def test_source_scope_filters_both_indexes_before_fusing():
    segment = TranscriptSegment(
        "seg", "meeting", "speaker", 0, 1000, "meeting evidence", 1, 1, Locale.EN
    )
    document = Fragment("frag", "document", None, 0, "document evidence", Locator(chars=(0, 17)), 1)
    retriever = RecordingRetriever(segment)
    fragments = RecordingFragmentIndex(document)
    service = QueryService(None, retriever, None, None, None, 50, fragment_index=fragments)
    scope = ResolvedQueryScope(QueryScopeMode.SOURCES, frozenset({"meeting", "document"}), False)

    evidence = service._retrieve(
        "workspace",
        "owner",
        QueryRequest("evidence", Locale.EN, scope=scope, citation_format=CitationFormat.SOURCE),
        None,
    )

    assert retriever.scopes == [scope.source_ids]
    assert fragments.scopes == [scope.source_ids]
    assert {(item.source_id, item.id) for item in evidence} == {
        ("meeting", "seg"),
        ("document", "frag"),
    }


def test_legacy_scope_keeps_document_index_out_of_the_path():
    segment = TranscriptSegment(
        "seg", "meeting", "speaker", 0, 1000, "meeting evidence", 1, 1, Locale.EN
    )
    retriever = RecordingRetriever(segment)
    fragments = RecordingFragmentIndex(
        Fragment("frag", "document", None, 0, "doc", Locator(chars=(0, 3)), 1)
    )
    service = QueryService(None, retriever, None, None, None, 50, fragment_index=fragments)

    evidence = service._retrieve("workspace", "owner", QueryRequest("evidence", Locale.EN), None)

    assert [(item.source_id, item.id) for item in evidence] == [("meeting", "seg")]
    assert fragments.scopes == []
