"""Traces, metrics and structured logs, wired to Azure Monitor (ADR-018).

The code only ever talks to the OpenTelemetry API (spans and metrics are no-ops until something
configures an exporter), so tests, the local engine and the Azure deployment run the same lines.
`configure()` installs Azure Monitor when a connection string exists.

Privacy rules, enforced here and not left to each call site:
- no transcript text, questions, answers, names or tokens in any attribute or log line;
- user and workspace ids leave the process only as a salted hash (a stable pseudonym that still
  lets an operator follow one user's requests, and that is useless without the salt);
- URLs are recorded without their query string, because a blob or batch-result link carries its
  credential there.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, Status, StatusCode

SERVICE_NAMESPACE = "nodo"
TRACER_NAME = "nodo_api"
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_salt = os.environ.get("NODO_TELEMETRY_SALT", "")
_configured = False


def pseudonym(value: str) -> str:
    """A stable, non-reversible tag for a user or workspace id."""
    return hashlib.sha256(f"{_salt}|{value}".encode()).hexdigest()[:12]


def strip_query(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0]


# --------------------------------------------------------------------------------------- spans


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """A span that records an exception's type (never its message) and marks itself failed."""
    with tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except BaseException as error:
            current.set_attribute("error.type", type(error).__name__)
            current.set_status(Status(StatusCode.ERROR))
            raise


def inject_context() -> dict[str, str]:
    """The W3C trace context of the current span, to carry across a queue."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


@contextmanager
def continued(carrier: Mapping[str, Any] | None) -> Iterator[None]:
    """Run inside the trace a message came from, so the worker's spans join the request's trace."""
    token = otel_context.attach(extract(dict(carrier or {})))
    try:
        yield
    finally:
        otel_context.detach(token)


# ------------------------------------------------------------------------------------- metrics


class Instruments:
    """The service's metrics, created on first use from the global meter provider."""

    def __init__(self) -> None:
        self._built = False

    def _build(self) -> None:
        meter = metrics.get_meter(TRACER_NAME)
        self.stage_seconds = meter.create_histogram(
            "nodo.ingestion.stage.duration", unit="s", description="Time per pipeline stage"
        )
        self.audio_seconds = meter.create_counter(
            "nodo.ingestion.audio", unit="s", description="Seconds of audio processed"
        )
        self.queue_age = meter.create_histogram(
            "nodo.queue.message.age",
            unit="s",
            description="Time a message waited before work began",
        )
        self.query_seconds = meter.create_histogram(
            "nodo.query.duration", unit="s", description="Question to answer"
        )
        self.query_degraded = meter.create_counter(
            "nodo.query.degraded", description="Answers returned without the language model"
        )
        self.llm_tokens = meter.create_counter(
            "nodo.llm.tokens", description="Language model tokens, by kind and purpose"
        )
        self.dependency_failures = meter.create_counter(
            "nodo.dependency.failures", description="Calls that ran out of retries, by dependency"
        )
        self.circuit_opened = meter.create_counter(
            "nodo.dependency.circuit_opened", description="Circuit breakers that opened"
        )
        self.shed = meter.create_counter(
            "nodo.requests.shed", description="Requests turned away because the replica was full"
        )
        self.retention_deleted = meter.create_counter(
            "nodo.retention.deleted", description="Meetings erased because their retention ended"
        )
        self.exports = meter.create_counter("nodo.exports", description="Data exports built")
        self.index_findings = meter.create_counter(
            "nodo.index.findings", description="Differences found between store, index and graph"
        )
        self._built = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or self._built:
            raise AttributeError(name)
        self._build()
        return getattr(self, name)


instruments = Instruments()


@contextmanager
def timed(histogram_name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Record the duration of a block in a named histogram; the caller may add attributes."""
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield extra
    except BaseException:
        outcome = "error"
        raise
    finally:
        getattr(instruments, histogram_name).record(
            time.perf_counter() - started, {**attributes, **extra, "outcome": outcome}
        )


@contextmanager
def stage(name: str, **attributes: Any) -> Iterator[Span]:
    """A pipeline stage: a span and a duration measurement under the same name."""
    with span(f"ingest.{name}", **attributes) as current, timed("stage_seconds", stage=name):
        yield current


# ------------------------------------------------------------------------------------ logging


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the trace ids so a log line finds its trace. Only the type of
    an exception is logged, never its message (messages carry data)."""

    ALLOWED_EXTRA = ("event", "dependency", "stage", "status_code", "duration_ms", "outcome")

    def format(self, record: logging.LogRecord) -> str:
        import json

        context = trace.get_current_span().get_span_context()
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if context.is_valid:
            entry["trace_id"] = format(context.trace_id, "032x")
            entry["span_id"] = format(context.span_id, "016x")
        if request_id_var.get():
            entry["request_id"] = request_id_var.get()
        for key in self.ALLOWED_EXTRA:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info and record.exc_info[0] is not None:
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("azure", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(
            logging.WARNING
        )  # their debug lines carry URLs and headers


# --------------------------------------------------------------------------------- configuration


def _redact_http(span_: Span, request: Any, *_: Any) -> None:
    url = getattr(request, "url", None)
    if url is not None:
        clean = strip_query(str(url))
        span_.set_attribute("http.url", clean)
        span_.set_attribute("url.full", clean)
        span_.set_attribute("url.query", "")


def configure(
    *,
    role: str,
    environment: str,
    version: str,
    connection_string: str | None,
    sampling_ratio: float = 1.0,
    span_exporter: Any = None,
    metric_reader: Any = None,
) -> bool:
    """Install the exporters once. Returns True if telemetry is being collected."""
    global _configured
    if _configured:
        return True
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create(
        {
            "service.name": f"{SERVICE_NAMESPACE}-{role}",
            "service.namespace": SERVICE_NAMESPACE,
            "service.version": version,
            "deployment.environment.name": environment,
        }
    )
    if span_exporter is not None or metric_reader is not None:  # tests and local debugging
        _install_sdk(resource, span_exporter, metric_reader)
    elif connection_string:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=connection_string,
            resource=resource,
            sampling_ratio=sampling_ratio,
            enable_live_metrics=False,
        )
    else:
        return False
    _instrument_libraries()
    _configured = True
    return True


def _install_sdk(resource: Any, span_exporter: Any, metric_reader: Any) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    if span_exporter is not None:
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
    if metric_reader is not None:
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))


def _instrument_libraries() -> None:
    """Dependency spans for the HTTP and database calls the SDKs make, with credentials removed."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(request_hook=_redact_http)
    except ImportError:  # pragma: no cover - optional package
        logging.getLogger("nodo_telemetry").info("httpx instrumentation not installed")
    try:
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(enable_commenter=False)
    except ImportError:  # pragma: no cover - optional package
        logging.getLogger("nodo_telemetry").info("psycopg instrumentation not installed")


def instrument_app(app: Any) -> None:
    """Server spans for every request of a FastAPI app, minus the health probes."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    except ImportError:  # pragma: no cover - optional package
        pass
