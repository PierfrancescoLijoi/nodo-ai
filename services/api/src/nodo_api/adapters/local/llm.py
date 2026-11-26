"""The local language model: Azure OpenAI's adapters pointed at llama.cpp.

The prompts, the JSON schemas, the citation building and the parsing are the same code that runs
against Azure, so the local backend can not drift from it. This module only adds what is local:
starting the model server when it is needed, taking the GPU lease, and the request options Qwen3
wants (no "thinking" text before the JSON, a low temperature)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nodo_api.adapters.azure.openai_extractor import (
    AzureOpenAiExtractor,
    AzureOpenAiFragmentExtractor,
)
from nodo_api.adapters.azure.openai_hierarchy import AzureOpenAiParentSuggester
from nodo_api.adapters.azure.openai_llm import AzureOpenAiLlm
from nodo_api.adapters.local.models import GpuArbiter, LlamaServer
from nodo_api.domain.graph import Entity, Extraction, Subgraph
from nodo_api.domain.models import Locale, TranscriptSegment
from nodo_api.domain.sources import Fragment, SectionSummary

REQUEST_TIMEOUT_SECONDS = 900
ANSWER_OPTIONS: dict[str, Any] = {
    "temperature": 0.2,
    "max_tokens": 1024,
    "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
}
EXTRACTION_OPTIONS: dict[str, Any] = {
    "temperature": 0.1,
    "max_tokens": 4096,
    "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
}
MODEL_NAME = "local"


class _LocalServer:
    def __init__(self, server: LlamaServer, arbiter: GpuArbiter) -> None:
        self._server = server
        self._arbiter = arbiter
        arbiter.register("llm", server.stop)

    def client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            base_url=self._server.ensure_started(),
            api_key="local",
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def lease(self):
        return self._arbiter.lease("llm")


class LocalLlm(_LocalServer):
    def __init__(
        self, server: LlamaServer, arbiter: GpuArbiter, options: dict[str, Any] | None = None
    ) -> None:
        super().__init__(server, arbiter)
        self._answer_options = options or ANSWER_OPTIONS

    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[TranscriptSegment | Fragment, ...],
        graph: Subgraph,
        summaries: tuple[SectionSummary, ...] = (),
    ) -> Any:
        with self.lease():
            llm = AzureOpenAiLlm(self.client(), MODEL_NAME, self._answer_options)
            return llm.answer(workspace_id, question, locale, evidence, graph, summaries)


class LocalExtractor(_LocalServer):
    def extract(
        self,
        segments: tuple[TranscriptSegment, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...],
        speakers: Mapping[str, str] | None = None,
    ) -> Extraction:
        with self.lease():
            extractor = AzureOpenAiExtractor(self.client(), MODEL_NAME, EXTRACTION_OPTIONS)
            return extractor.extract(segments, locale, known_decisions, speakers)


class LocalFragmentExtractor(_LocalServer):
    """Entities and relations from document fragments, with the same model and options."""

    def extract(
        self,
        fragments: tuple[Fragment, ...],
        section_path: tuple[str, ...],
        locale: Locale,
        known_decisions: tuple[Entity, ...] = (),
    ) -> Extraction:
        with self.lease():
            extractor = AzureOpenAiFragmentExtractor(self.client(), MODEL_NAME, EXTRACTION_OPTIONS)
            return extractor.extract(fragments, section_path, locale, known_decisions)


class LocalParentSuggester(_LocalServer):
    def suggest(
        self,
        concept: Entity,
        candidates: tuple[Entity, ...],
        fragments: tuple[Fragment, ...],
        locale: Locale,
    ):
        with self.lease():
            suggester = AzureOpenAiParentSuggester(self.client(), MODEL_NAME, EXTRACTION_OPTIONS)
            return suggester.suggest(concept, candidates, fragments, locale)
