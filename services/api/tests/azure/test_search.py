from __future__ import annotations

from dataclasses import replace

from nodo_api.adapters.azure.search import (
    TEXT_FIELDS,
    AzureSearchRetriever,
    access_filter,
    build_index,
    document_key,
    ensure_index,
    odata_string,
)
from nodo_api.domain.models import Locale, TranscriptSegment


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeSearchClient:
    def __init__(self, pages: list[list[dict]] | None = None) -> None:
        self.searches: list[dict] = []
        self.uploaded: list[dict] = []
        self.deleted: list[dict] = []
        self.pages = pages or []

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return self.pages.pop(0) if self.pages else []

    def merge_or_upload_documents(self, documents: list[dict]) -> None:
        self.uploaded.extend(documents)

    def delete_documents(self, documents: list[dict]) -> None:
        self.deleted.extend(documents)


def _segment(locale: Locale = Locale.PL, text: str = "Kolejka asynchroniczna") -> TranscriptSegment:
    return TranscriptSegment("seg-1", "m1", "S1", 1000, 2000, text, 0.9, 1, locale, "NODO")


def test_index_has_one_analyzer_field_per_language_and_a_vector_field() -> None:
    index = build_index("segments", 1536)
    analyzers = {f.name: f.analyzer_name for f in index.fields if f.name.startswith("text_")}
    assert analyzers == {
        "text_en": "en.microsoft",
        "text_pl": "pl.microsoft",
        "text_it": "it.microsoft",
        "text_da": "da.microsoft",
    }
    vector = next(f for f in index.fields if f.name == "vector")
    assert vector.vector_search_dimensions == 1536
    filterable = {f.name for f in index.fields if f.filterable}
    assert {"workspace_id", "meeting_id"} <= filterable


def test_ensure_index_creates_or_updates() -> None:
    seen = []

    class Client:
        def create_or_update_index(self, index) -> None:
            seen.append(index.name)

    ensure_index(Client(), "segments", 1536)
    assert seen == ["segments"]


def test_filter_scopes_by_workspace_and_owner_and_escapes_quotes() -> None:
    flt = access_filter("ws'a", "al'ice", "O'Neil")
    assert flt == "workspace_id eq 'ws''a' and owner_id eq 'al''ice' and project eq 'O''Neil'"
    assert odata_string("a'b") == "'a''b'"


def test_index_stores_the_text_in_the_field_of_its_language_with_a_vector() -> None:
    client, embedder = FakeSearchClient(), FakeEmbedder()
    AzureSearchRetriever(client, embedder).index("ws-a", "alice", (_segment(Locale.PL),))
    doc = client.uploaded[0]
    assert doc["text_pl"] == "Kolejka asynchroniczna"
    assert not any(doc.get(f) for f in TEXT_FIELDS if f != "text_pl")
    assert doc["vector"] == [0.1, 0.2, 0.3]
    assert doc["workspace_id"] == "ws-a"
    assert doc["owner_id"] == "alice"
    assert doc["id"] == document_key("ws-a", "m1", "seg-1")
    assert doc["id"].isalnum()  # a valid Search key, whatever the ids contained


def test_a_long_transcript_is_embedded_and_uploaded_in_batches() -> None:
    client, embedder = FakeSearchClient(), FakeEmbedder()
    segments = tuple(
        replace(_segment(Locale.EN), id=f"seg-{i}", text=f"sentence {i}") for i in range(600)
    )
    AzureSearchRetriever(client, embedder).index("ws-a", "alice", segments)
    assert [len(call) for call in embedder.calls] == [256, 256, 88]
    assert len(client.uploaded) == 600
    assert len({doc["id"] for doc in client.uploaded}) == 600


def test_search_is_hybrid_and_filtered_before_ranking() -> None:
    hit = {
        "meeting_id": "m1",
        "segment_id": "seg-1",
        "speaker_id": "S1",
        "start_ms": 1000,
        "end_ms": 2000,
        "version": 1,
        "locale": "da",
        "text_da": "Den asynkrone kø",
        "confidence": 0.9,
    }
    client = FakeSearchClient([[hit]])
    retriever = AzureSearchRetriever(client, FakeEmbedder())
    result = retriever.search("ws-a", "alice", "asynkron kø", {"project": "NODO"}, 5)
    call = client.searches[0]
    assert call["search_text"] == "asynkron kø"
    assert call["search_fields"] == TEXT_FIELDS
    assert call["top"] == 5
    assert call["vector_queries"][0].fields == "vector"
    assert "workspace_id eq 'ws-a'" in call["filter"]
    assert "owner_id eq 'alice'" in call["filter"]
    assert "search.in" not in call["filter"]
    assert "project eq 'NODO'" in call["filter"]
    assert result[0].text == "Den asynkrone kø"
    assert result[0].locale is Locale.DA


def test_the_access_filter_does_not_grow_with_the_number_of_meetings() -> None:
    assert access_filter("ws-a", "alice") == "workspace_id eq 'ws-a' and owner_id eq 'alice'"
    hostile = access_filter("ws-a", "x' or owner_id ne 'y")
    assert "owner_id eq 'x'' or owner_id ne ''y'" in hostile  # quotes are doubled, not obeyed


def test_erase_removes_every_page_of_the_meeting() -> None:
    client = FakeSearchClient([[{"id": "a"}, {"id": "b"}], [{"id": "c"}], []])
    AzureSearchRetriever(client, FakeEmbedder()).erase("ws-a", "m1")
    assert [d["id"] for d in client.deleted] == ["a", "b", "c"]
    assert "meeting_id eq 'm1'" in client.searches[0]["filter"]


def test_count_asks_for_the_total_of_one_meeting_and_fetches_no_documents() -> None:
    class CountingClient(FakeSearchClient):
        def search(self, **kwargs):
            self.searches.append(kwargs)

            class Results:
                def get_count(self_inner) -> int:
                    return 42

            return Results()

    client = CountingClient()
    assert AzureSearchRetriever(client, FakeEmbedder()).count("ws-a", "it's") == 42
    (call,) = client.searches
    assert call["include_total_count"] is True and call["top"] == 0
    assert call["filter"] == "workspace_id eq 'ws-a' and meeting_id eq 'it''s'"
