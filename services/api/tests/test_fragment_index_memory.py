from __future__ import annotations

import uuid

import pytest

from nodo_api.adapters.memory import (
    MemoryFragmentIndex,
    MemoryFragmentRepository,
    MemoryMeetingRepository,
    MemorySourceRepository,
    MemoryTranscriptRepository,
    MockRetriever,
)
from tests.fragment_index_contract import FragmentIndexContract


class TestMemoryFragmentIndex(FragmentIndexContract):
    @pytest.fixture
    def sources(self):
        return MemorySourceRepository(MemoryMeetingRepository())

    @pytest.fixture
    def fragments(self, sources):
        return MemoryFragmentRepository(sources, MemoryTranscriptRepository())

    @pytest.fixture
    def index(self, sources):
        return MemoryFragmentIndex(sources)

    @pytest.fixture
    def retriever(self):
        return MockRetriever()

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
