"""The local language model: Azure OpenAI's adapters pointed at llama.cpp.

The prompts, the JSON schemas, the citation building and the parsing are the same code that runs
against Azure, so the local backend can not drift from it. This module only adds what is local:
starting the model server when it is needed, taking the GPU lease, and the request options Qwen3
wants (no "thinking" text before the JSON, a low temperature)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nodo_api.adapters.azure.openai_extractor import AzureOpenAiExtractor
from nodo_api.adapters.azure.openai_llm import AzureOpenAiLlm
from nodo_api.adapters.local.models import GpuArbiter, LlamaServer
from nodo_api.domain.graph import Entity, Extraction, Subgraph
from nodo_api.domain.models import Locale, TranscriptSegment

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
    def answer(
        self,
        workspace_id: str,
        question: str,
        locale: Locale,
        evidence: tuple[TranscriptSegment, ...],
        graph: Subgraph,
    ) -> Any:
        with self.lease():
            llm = AzureOpenAiLlm(self.client(), MODEL_NAME, ANSWER_OPTIONS)
            return llm.answer(workspace_id, question, locale, evidence, graph)


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
