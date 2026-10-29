"""What leaves the process as telemetry: its shape, and what must never be in it."""

from __future__ import annotations

import json
import logging
import sys

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nodo_api import telemetry
from nodo_api.adapters.memory import MemorySpeech
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.resilience import DependencyUnavailable, RetryPolicy, make_guard
from nodo_api.services.ingestion import IngestionService
from nodo_api.worker import Worker

EXPORTER = InMemorySpanExporter()
READER = InMemoryMetricReader()


@pytest.fixture(scope="module", autouse=True)
def providers():
    """OpenTelemetry providers can be set once per process, so the module installs them once."""
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[READER]))
    telemetry.instruments._built = False  # build instruments on the provider installed above
    yield


@pytest.fixture(autouse=True)
def clean():
    EXPORTER.clear()


def _points(name: str) -> list:
    data = READER.get_metrics_data()
    found = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    found += list(metric.data.data_points)
    return found


# --- privacy -------------------------------------------------------------------------------------


def test_pseudonyms_are_stable_short_and_not_the_id() -> None:
    first = telemetry.pseudonym("alice@example.com")
    assert first == telemetry.pseudonym("alice@example.com")
    assert len(first) == 12 and "alice" not in first
    assert telemetry.pseudonym("bob") != first


def test_urls_lose_their_query_string_because_the_credential_lives_there() -> None:
    url = "https://a.blob.core.windows.net/c/b?sv=1&sig=SECRET"
    assert telemetry.strip_query(url) == "https://a.blob.core.windows.net/c/b"

    class Recorded:
        def __init__(self) -> None:
            self.attributes: dict = {}

        def set_attribute(self, key, value) -> None:
            self.attributes[key] = value

    class Request:
        url = "https://speech.example/results/file?sig=SECRET&se=2026"

    recorded = Recorded()
    telemetry._redact_http(recorded, Request())
    assert "SECRET" not in json.dumps(recorded.attributes)
    assert recorded.attributes["http.url"] == "https://speech.example/results/file"


def test_a_failing_span_records_the_error_type_and_never_the_message() -> None:
    with pytest.raises(ValueError), telemetry.span("work", locale="it"):
        raise ValueError("the patient is Mario Rossi")
    (finished,) = EXPORTER.get_finished_spans()
    assert finished.attributes["error.type"] == "ValueError"
    assert finished.attributes["locale"] == "it"
    assert finished.status.status_code.name == "ERROR"
    assert "Mario" not in json.dumps(dict(finished.attributes)) + str(finished.events)


# --- logs ----------------------------------------------------------------------------------------


def _log_line(record: logging.LogRecord) -> dict:
    return json.loads(telemetry.JsonFormatter().format(record))


def test_logs_are_json_with_trace_ids_and_only_the_type_of_an_exception() -> None:
    logger = logging.getLogger("t")
    with telemetry.span("request"):
        try:
            raise RuntimeError("secret transcript text")
        except RuntimeError:
            record = logger.makeRecord(
                "t",
                logging.ERROR,
                __file__,
                1,
                "failed",
                (),
                sys.exc_info(),
                extra={"stage": "analyze", "unlisted": "dropped"},
            )
        entry = _log_line(record)
    assert entry["level"] == "ERROR" and entry["message"] == "failed"
    assert len(entry["trace_id"]) == 32 and len(entry["span_id"]) == 16
    assert entry["error_type"] == "RuntimeError" and entry["stage"] == "analyze"
    assert "unlisted" not in entry and "secret" not in json.dumps(entry)


def test_the_request_id_is_in_every_line_of_a_request() -> None:
    token = telemetry.request_id_var.set("req-12345678")
    try:
        record = logging.getLogger("t").makeRecord("t", logging.INFO, __file__, 1, "hi", (), None)
        assert _log_line(record)["request_id"] == "req-12345678"
    finally:
        telemetry.request_id_var.reset(token)


# --- tracing across a queue ----------------------------------------------------------------------


def test_a_worker_span_joins_the_trace_of_the_request_that_queued_the_job() -> None:
    with telemetry.span("http.request"):
        carrier = telemetry.inject_context()  # what goes into the queue message
    assert "traceparent" in carrier
    with telemetry.continued(carrier), telemetry.span("worker.transcribe"):
        pass
    by_name = {s.name: s for s in EXPORTER.get_finished_spans()}
    request, worker = by_name["http.request"], by_name["worker.transcribe"]
    assert worker.context.trace_id == request.context.trace_id
    assert worker.parent.span_id == request.context.span_id


def test_a_message_without_trace_context_still_runs() -> None:
    with telemetry.continued(None), telemetry.span("worker.transcribe"):
        pass
    assert len(EXPORTER.get_finished_spans()) == 1


# --- the pipeline --------------------------------------------------------------------------------


def test_ingestion_emits_a_span_and_a_duration_per_stage_and_counts_audio(app, attestation) -> None:
    container = app.state.container
    meeting = container.meeting_service.create("workspace-a", "user-a", "tele-key-1", attestation)
    container.meeting_service.complete("workspace-a", "user-a", meeting.id)
    ingestion = IngestionService(
        container.meetings,
        container.transcripts,
        container.blobs,
        container.retriever,
        MemorySpeech(),
        ("en-GB",),
        KeywordExtractor(),
        container.graph,
    )
    before = sum(p.value for p in _points("nodo.ingestion.audio"))
    assert ingestion.process("workspace-a", meeting.id)
    names = {s.name for s in EXPORTER.get_finished_spans()}
    assert {"ingest.transcribe", "ingest.index", "ingest.analyze"} <= names
    stages = {p.attributes["stage"] for p in _points("nodo.ingestion.stage.duration")}
    assert {"transcribe", "index", "analyze"} <= stages
    assert sum(p.value for p in _points("nodo.ingestion.audio")) > before
    for finished in EXPORTER.get_finished_spans():  # nothing from the transcript in any attribute
        assert "ingestion queue" not in json.dumps(dict(finished.attributes))


def test_a_question_is_one_trace_with_a_span_per_step(client, auth_headers) -> None:
    payload = {"question": "ingestion pipeline", "ui_locale": "en"}
    client.post("/v1/query", headers=auth_headers, json=payload)
    names = {s.name for s in EXPORTER.get_finished_spans()}
    assert {"query", "query.retrieve", "query.graph", "query.model", "query.validate"} <= names
    assert _points("nodo.query.duration")
    recorded = [dict(s.attributes) for s in EXPORTER.get_finished_spans() if s.name == "query"]
    assert not any("pipeline" in json.dumps(a) for a in recorded)  # the question is not recorded


def test_authenticated_requests_carry_a_pseudonym_not_the_user(client, auth_headers) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(client.app)
    try:
        client.get("/v1/meetings", headers=auth_headers)
    finally:
        FastAPIInstrumentor.uninstrument_app(client.app)
    servers = [s for s in EXPORTER.get_finished_spans() if s.kind.name == "SERVER"]
    attributes = dict(servers[0].attributes)
    assert attributes["enduser.pseudo_id"] == telemetry.pseudonym("user-a")
    assert "user-a" not in json.dumps(attributes, default=str)


def test_a_dependency_that_runs_out_of_retries_is_counted() -> None:
    guard = make_guard("search-test", limit=2, retry=RetryPolicy(attempts=1))

    class Down(Exception):
        status_code = 503

    def fail():
        raise Down

    with pytest.raises(DependencyUnavailable):
        guard.call(fail)
    counted = {p.attributes.get("dependency") for p in _points("nodo.dependency.failures")}
    assert "search-test" in counted


def test_a_message_without_an_enqueue_time_is_not_an_error() -> None:
    Worker._record_queue_age(object())  # nothing to measure: nothing recorded, no crash
