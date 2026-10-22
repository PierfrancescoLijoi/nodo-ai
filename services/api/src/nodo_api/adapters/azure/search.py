"""Azure AI Search adapter: hybrid (lexical plus vector) retrieval with the access filter applied
inside the query. One text field per language, each with its own Microsoft analyzer, because a
field accepts only one analyzer (docs/AZURE-ARCHITECTURE.md section 7)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.ports import Embedder

ANALYZERS = {
    Locale.EN: "en.microsoft",
    Locale.PL: "pl.microsoft",
    Locale.IT: "it.microsoft",
    Locale.DA: "da.microsoft",
}
TEXT_FIELDS = [f"text_{locale.value}" for locale in Locale]
SELECT_FIELDS = [
    "workspace_id",
    "meeting_id",
    "segment_id",
    "speaker_id",
    "start_ms",
    "end_ms",
    "version",
    "project",
    "locale",
    "confidence",
    *TEXT_FIELDS,
]
ERASE_PAGE = 500
INDEX_BATCH = 256  # one embedding request and one upload per 256 segments
VECTOR_PROFILE = "nodo-vector"


def document_key(workspace_id: str, meeting_id: str, segment_id: str) -> str:
    """Search keys allow only a small character set, so the key is a hash of the identity."""
    return hashlib.sha256(f"{workspace_id}|{meeting_id}|{segment_id}".encode()).hexdigest()


def odata_string(value: str) -> str:
    """Quote a value for an OData filter; a single quote is escaped by doubling it."""
    return "'" + value.replace("'", "''") + "'"


def access_filter(workspace_id: str, owner_id: str, project: str | None = None) -> str:
    """Two equality clauses however many meetings the owner has (a meeting list in the filter would
    grow with the history and slow every question)."""
    clauses = [
        f"workspace_id eq {odata_string(workspace_id)}",
        f"owner_id eq {odata_string(owner_id)}",
    ]
    if project:
        clauses.append(f"project eq {odata_string(project)}")
    return " and ".join(clauses)


def build_index(name: str, dimensions: int) -> SearchIndex:
    fields = [
        SearchField(name="id", type=SearchFieldDataType.STRING, key=True, filterable=True),
        SearchField(name="workspace_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchField(name="owner_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchField(name="meeting_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchField(name="segment_id", type=SearchFieldDataType.STRING, retrievable=True),
        SearchField(name="speaker_id", type=SearchFieldDataType.STRING, retrievable=True),
        SearchField(name="start_ms", type=SearchFieldDataType.INT32, retrievable=True),
        SearchField(name="end_ms", type=SearchFieldDataType.INT32, retrievable=True),
        SearchField(name="version", type=SearchFieldDataType.INT32, retrievable=True),
        SearchField(name="confidence", type=SearchFieldDataType.DOUBLE, retrievable=True),
        SearchField(name="project", type=SearchFieldDataType.STRING, filterable=True),
        SearchField(name="locale", type=SearchFieldDataType.STRING, filterable=True),
        *[
            SearchField(
                name=f"text_{locale.value}",
                type=SearchFieldDataType.STRING,
                searchable=True,
                analyzer_name=analyzer,
            )
            for locale, analyzer in ANALYZERS.items()
        ],
        SearchField(
            name="vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.SINGLE),
            searchable=True,
            retrievable=False,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="hnsw")],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


def ensure_index(index_client: Any, name: str, dimensions: int) -> None:
    index_client.create_or_update_index(build_index(name, dimensions))


def _segment(doc: dict[str, Any]) -> TranscriptSegment:
    locale = Locale(doc["locale"])
    return TranscriptSegment(
        id=doc["segment_id"],
        meeting_id=doc["meeting_id"],
        speaker_id=doc["speaker_id"],
        start_ms=doc["start_ms"],
        end_ms=doc["end_ms"],
        text=doc.get(f"text_{locale.value}") or "",
        confidence=doc.get("confidence") or 0.0,
        version=doc["version"],
        locale=locale,
        project=doc.get("project"),
    )


def _document(
    workspace_id: str, owner_id: str, segment: TranscriptSegment, vector: list[float]
) -> dict[str, Any]:
    return {
        "id": document_key(workspace_id, segment.meeting_id, segment.id),
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "meeting_id": segment.meeting_id,
        "segment_id": segment.id,
        "speaker_id": segment.speaker_id,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "version": segment.version,
        "confidence": segment.confidence,
        "project": segment.project,
        "locale": segment.locale.value,
        f"text_{segment.locale.value}": segment.text,
        "vector": vector,
    }


class AzureSearchRetriever:
    def __init__(self, search_client: Any, embedder: Embedder) -> None:
        self._client = search_client
        self._embedder = embedder

    def index(
        self, workspace_id: str, owner_id: str, segments: Sequence[TranscriptSegment]
    ) -> None:
        for start in range(0, len(segments), INDEX_BATCH):
            batch = segments[start : start + INDEX_BATCH]
            vectors = self._embedder.embed([s.text for s in batch])
            documents = [
                _document(workspace_id, owner_id, segment, vector)
                for segment, vector in zip(batch, vectors, strict=True)
            ]
            self._client.merge_or_upload_documents(documents)

    def search(
        self,
        workspace_id: str,
        owner_id: str,
        query: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> tuple[TranscriptSegment, ...]:
        vector = self._embedder.embed([query])[0]
        results = self._client.search(
            search_text=query,
            search_fields=TEXT_FIELDS,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=limit, fields="vector")
            ],
            filter=access_filter(workspace_id, owner_id, (filters or {}).get("project")),
            select=SELECT_FIELDS,
            top=limit,
        )
        return tuple(_segment(doc) for doc in results)

    def erase(self, workspace_id: str, meeting_id: str) -> None:
        flt = (
            f"workspace_id eq {odata_string(workspace_id)}"
            f" and meeting_id eq {odata_string(meeting_id)}"
        )
        while True:
            page = list(
                self._client.search(search_text="*", filter=flt, select=["id"], top=ERASE_PAGE)
            )
            if not page:
                return
            self._client.delete_documents([{"id": doc["id"]} for doc in page])
