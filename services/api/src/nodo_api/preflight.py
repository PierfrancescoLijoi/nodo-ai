"""Is this deployment actually connected? (ADR-023)

    python -m nodo_api.admin preflight api       # as the API's identity
    python -m nodo_api.admin preflight worker    # as the worker's identity

Run once after the first deployment and after any change to identities, networks or models. Each
check uses the same code production uses, so a pass means that path works, and a failure says what
is probably wrong and where to look. Checks leave nothing behind: test objects live under a
workspace named `preflight` and are removed, the queue message is scheduled an hour out and
cancelled, and no model call carries real data. Nothing here prints a secret or a transcript."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

PREFLIGHT_WORKSPACE = "preflight"
PROPAGATION_SECONDS = 20  # search documents become visible a few seconds after they are written

SCOPES = {
    "postgres": "https://ossrdbms-aad.database.windows.net/.default",
    "storage": "https://storage.azure.com/.default",
    "search": "https://search.azure.com/.default",
    "cognitive": "https://cognitiveservices.azure.com/.default",
    "servicebus": "https://servicebus.azure.net/.default",
}
_SIGNATURE = re.compile(r"(sig=)[^&\s\"']+")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")
_TOKEN_PARAM = re.compile(r"((?:token|key|secret)=)[^&\s\"']+", re.IGNORECASE)


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


class CheckFailed(Exception):
    """A check that ran and found something wrong; the message says what."""


class CheckSkipped(Exception):
    """A check that has nothing to look at in this deployment (for example no public host)."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    hint: str = ""
    millis: int = 0


@dataclass
class Context:
    """What the checks need, built from the same wiring production uses."""

    role: str
    settings: Any
    credential: Any
    db: Any = None
    blobs: Any = None
    blob_service: Any = None
    servicebus: Any = None
    search_index: Any = None
    retriever: Any = None
    embedder: Any = None
    llm: Any = None
    extractor: Any = None
    http: Any = None
    public_host: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    roles: tuple[str, ...]
    hint: str
    run: Callable[[Context], str]


def redact(text: str) -> str:
    """Signatures and tokens never reach a log, even by accident."""
    text = _SIGNATURE.sub(r"\1***", text)
    text = _BEARER.sub(r"\1***", text)
    return _TOKEN_PARAM.sub(r"\1***", text)


# ---------------------------------------------------------------------------------------- checks


def _identity(ctx: Context) -> str:
    failed = []
    for name, scope in SCOPES.items():
        try:
            ctx.credential.get_token(scope)
        except Exception:
            failed.append(name)
    if failed:
        raise CheckFailed(f"no access token for: {', '.join(failed)}")
    return f"tokens for {', '.join(SCOPES)}"


def _postgres_connect(ctx: Context) -> str:
    with ctx.db.admin() as conn:
        row = conn.execute("SELECT current_user AS u, version() AS v").fetchone()
    if row["u"] != ctx.settings.azure_postgres_user:
        raise CheckFailed(f"connected as {row['u']}, expected {ctx.settings.azure_postgres_user}")
    return f"as {row['u']} on {str(row['v']).split(',')[0]}"


def _postgres_schema(ctx: Context) -> str:
    from nodo_api.adapters.azure.migrate import migration_files

    with ctx.db.admin() as conn:
        applied = {
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        extensions = {
            r["extname"] for r in conn.execute("SELECT extname FROM pg_extension").fetchall()
        }
    wanted = {version for version, _, _ in migration_files()}
    missing = sorted(wanted - applied)
    if missing:
        raise CheckFailed(f"migrations not applied: {missing}")
    absent = sorted({"vector", "pg_trgm"} - extensions)
    if absent:
        raise CheckFailed(f"extensions missing: {', '.join(absent)}")
    return f"{len(wanted)} migrations applied; vector and pg_trgm present"


_UNPROTECTED = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND EXISTS (SELECT 1 FROM information_schema.columns col WHERE col.table_schema = 'public'
              AND col.table_name = c.relname AND col.column_name = 'workspace_id')
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
"""


def _postgres_isolation(ctx: Context) -> str:
    with ctx.db.admin() as conn:
        open_tables = [r["relname"] for r in conn.execute(_UNPROTECTED).fetchall()]
        outside = conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"]
    if open_tables:
        raise CheckFailed(f"row level security is not forced on: {', '.join(sorted(open_tables))}")
    with ctx.db.workspace(f"preflight-{uuid.uuid4().hex[:8]}") as conn:
        inside = conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"]
    if outside or inside:
        raise CheckFailed(f"a connection with no workspace sees {outside}, a foreign one {inside}")
    return "every workspace table forces row level security; a stranger's workspace sees nothing"


def _blob_roundtrip(ctx: Context) -> str:
    meeting = f"preflight-{uuid.uuid4().hex[:12]}"
    payload = b"nodo preflight " + uuid.uuid4().bytes
    upload = ctx.blobs.create_upload_session(PREFLIGHT_WORKSPACE, meeting)
    try:
        put = ctx.http.put(
            upload["upload_url"], content=payload, headers={"x-ms-blob-type": "BlockBlob"}
        )
        if put.status_code != 201:
            raise CheckFailed(f"upload with the write link answered {put.status_code}")
        got = ctx.http.get(ctx.blobs.create_read_url(PREFLIGHT_WORKSPACE, meeting))
        if got.status_code != 200 or got.content != payload:
            raise CheckFailed(f"read with the read link answered {got.status_code}")
        bare = ctx.http.get(upload["upload_url"].split("?", 1)[0])
        if bare.status_code == 200:
            raise CheckFailed("the blob can be read without a link: anonymous access is on")
    finally:
        ctx.blobs.erase(PREFLIGHT_WORKSPACE, meeting)
    return "write link, read link and refusal without a link all behave"


def _servicebus_send(ctx: Context) -> str:
    from azure.servicebus import ServiceBusMessage

    message = ServiceBusMessage(json.dumps({"job_type": "preflight"}), subject="preflight")
    with ctx.servicebus.get_queue_sender(ctx.settings.azure_servicebus_queue) as sender:
        numbers = sender.schedule_messages(message, datetime.now(UTC) + timedelta(hours=1))
        sender.cancel_scheduled_messages(numbers)
    return "scheduled a message an hour out and cancelled it"


def _servicebus_receive(ctx: Context) -> str:
    with ctx.servicebus.get_queue_receiver(
        ctx.settings.azure_servicebus_queue, max_wait_time=5
    ) as receiver:
        receiver.peek_messages(max_message_count=1)
    return "can peek the queue"


def _search_index(ctx: Context) -> str:
    from nodo_api.adapters.azure.search import ANALYZERS, VECTOR_PROFILE, build_index

    index = ctx.search_index.get_index(ctx.settings.search_index_name)
    have = {f.name: f for f in index.fields}
    wanted = {f.name for f in build_index("x", ctx.settings.openai_embedding_dimensions).fields}
    absent = sorted(wanted - set(have))
    if absent:
        raise CheckFailed(f"index fields missing: {', '.join(absent)} (run the bootstrap job)")
    vector = have["vector"]
    if vector.vector_search_dimensions != ctx.settings.openai_embedding_dimensions:
        raise CheckFailed(
            f"the index holds vectors of {vector.vector_search_dimensions}, "
            f"the embedding setting is {ctx.settings.openai_embedding_dimensions}"
        )
    if vector.vector_search_profile_name != VECTOR_PROFILE:
        raise CheckFailed("the vector field has the wrong profile")
    for locale, analyzer in ANALYZERS.items():
        if have[f"text_{locale.value}"].analyzer_name != analyzer:
            raise CheckFailed(f"text_{locale.value} does not use the {analyzer} analyzer")
    return f"{len(have)} fields, {vector.vector_search_dimensions}-dimension vectors, 4 analyzers"


def _embedding(ctx: Context) -> str:
    vector = ctx.embedder.embed(["nodo preflight"])[0]
    expected = ctx.settings.openai_embedding_dimensions
    if len(vector) != expected:
        raise CheckFailed(
            f"the embedding model returned {len(vector)} numbers, expected {expected}"
        )
    return f"{expected} dimensions"


def _search_roundtrip(ctx: Context) -> str:
    from nodo_api.domain.models import Locale, TranscriptSegment

    meeting = f"preflight-{uuid.uuid4().hex[:12]}"
    segment = TranscriptSegment(
        "seg-00001", meeting, "S1", 0, 1000, "preflight roundtrip sentence", 1.0, 1, Locale.EN
    )
    ctx.retriever.index(PREFLIGHT_WORKSPACE, "preflight-owner", [segment])
    try:
        if not _eventually(lambda: ctx.retriever.count(PREFLIGHT_WORKSPACE, meeting) == 1):
            raise CheckFailed("the document was written but never became visible")
        found = _eventually(
            lambda: any(
                s.meeting_id == meeting
                for s in ctx.retriever.search(
                    PREFLIGHT_WORKSPACE, "preflight-owner", "preflight roundtrip", None, 5
                )
            )
        )
        if not found:
            raise CheckFailed("the document is in the index but a query does not find it")
    finally:
        ctx.retriever.erase(PREFLIGHT_WORKSPACE, meeting)
    if not _eventually(lambda: ctx.retriever.count(PREFLIGHT_WORKSPACE, meeting) == 0):
        raise CheckFailed("the test document was not removed")
    return "indexed, counted, found by a hybrid query and removed"


def _eventually(condition: Callable[[], bool], seconds: float = PROPAGATION_SECONDS) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if condition():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(1.0)


def _model_answer(ctx: Context) -> str:
    from nodo_api.domain.graph import Subgraph
    from nodo_api.domain.models import Locale, TranscriptSegment

    evidence = (
        TranscriptSegment(
            "seg-1",
            "m",
            "S1",
            0,
            2000,
            "Sara will benchmark the retries by Friday.",
            1.0,
            1,
            Locale.EN,
        ),
    )
    answer = ctx.llm.answer(
        PREFLIGHT_WORKSPACE, "Who will benchmark the retries?", Locale.EN, evidence, Subgraph()
    )
    if not answer.citations:
        raise CheckFailed("the chat model answered but produced no usable citation")
    return "the strict answer schema is accepted and a grounded answer comes back"


def _model_extraction(ctx: Context) -> str:
    from nodo_api.domain.models import Locale, TranscriptSegment

    lines = (
        "Marco proposes to keep the ingestion queue synchronous.",
        "Sara agrees and will benchmark the retries by Friday.",
    )
    segments = tuple(
        TranscriptSegment(
            f"seg-{n}", "m", f"S{n}", n * 3000, n * 3000 + 2000, text, 1.0, 1, Locale.EN
        )
        for n, text in enumerate(lines, start=1)
    )
    extraction = ctx.extractor.extract(segments, Locale.EN, (), {})
    return f"the strict extraction schema is accepted ({len(extraction.entities)} entities)"


def _speech(ctx: Context) -> str:
    from nodo_api.adapters.azure.speech import API_VERSION

    token = ctx.credential.get_token(SCOPES["cognitive"]).token
    response = ctx.http.get(
        f"{ctx.settings.azure_speech_endpoint.rstrip('/')}/speechtotext/transcriptions",
        params={"api-version": API_VERSION, "top": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != 200:
        raise CheckFailed(f"listing transcriptions answered {response.status_code}")
    return f"transcription API {API_VERSION} answers"


def _gateway(ctx: Context) -> str:
    if not ctx.public_host:
        raise CheckSkipped("NODO_PREFLIGHT_PUBLIC_HOST is not set (basic tier has no public edge)")
    response = ctx.http.get(f"https://{ctx.public_host}/readyz")
    if response.status_code != 200:
        raise CheckFailed(f"https://{ctx.public_host}/readyz answered {response.status_code}")
    return "edge, gateway and API answer /readyz"


BOTH = ("api", "worker")
CHECKS: tuple[Check, ...] = (
    Check(
        "identity",
        BOTH,
        "Is the managed identity attached to the app or job? `az containerapp show -n <app> --query identity`.",
        _identity,
    ),
    Check(
        "postgres_connect",
        BOTH,
        "Run the bootstrap job: it creates a login role named after the identity (pgaadauth_create_principal). Check the private DNS zone and the firewall.",
        _postgres_connect,
    ),
    Check(
        "postgres_schema",
        BOTH,
        "Run the bootstrap job (migrations) and check azure.extensions allows VECTOR and PG_TRGM.",
        _postgres_schema,
    ),
    Check(
        "postgres_isolation",
        BOTH,
        "A migration did not run to the end, or a table was created by hand without row level security.",
        _postgres_isolation,
    ),
    Check(
        "blob_roundtrip",
        BOTH,
        "The identity needs Storage Blob Data Contributor on the audio account; check the private endpoint and that shared key access is off.",
        _blob_roundtrip,
    ),
    Check(
        "servicebus_send",
        ("api",),
        "The API identity needs Azure Service Bus Data Sender on the namespace (or the alias); check the private endpoint DNS.",
        _servicebus_send,
    ),
    Check(
        "servicebus_receive",
        ("worker",),
        "The worker identity needs Azure Service Bus Data Receiver; check the queue name.",
        _servicebus_receive,
    ),
    Check(
        "search_index",
        BOTH,
        "Run the bootstrap job (ensure-index); the embedding dimensions setting must equal the index.",
        _search_index,
    ),
    Check(
        "embedding",
        BOTH,
        "Check the embedding deployment name, its quota, the Cognitive Services OpenAI User role and the gateway policy.",
        _embedding,
    ),
    Check(
        "search_roundtrip",
        BOTH,
        "The identity needs Search Index Data Contributor; check the private endpoint.",
        _search_roundtrip,
    ),
    Check(
        "model_answer",
        BOTH,
        "The chat deployment must support structured outputs (strict JSON schema) with the API version in settings.",
        _model_answer,
    ),
    Check(
        "model_extraction",
        ("worker",),
        "The extraction schema is strict: the deployment and API version must support structured outputs.",
        _model_extraction,
    ),
    Check(
        "speech",
        ("worker",),
        "The worker needs Cognitive Services Speech User on the Speech account; check the custom subdomain endpoint.",
        _speech,
    ),
    Check(
        "gateway",
        ("api",),
        "Front Door, the gateway network rule, the Front Door id named value and the /readyz operation.",
        _gateway,
    ),
)


# ----------------------------------------------------------------------------------------- runner


def run_checks(ctx: Context, only: Sequence[str] | None = None) -> list[CheckResult]:
    results = []
    for check in CHECKS:
        if only is not None and check.name not in only:
            continue
        if ctx.role not in check.roles:
            results.append(CheckResult(check.name, Status.SKIP, f"not run as {ctx.role}"))
            continue
        began = time.monotonic()
        try:
            detail = check.run(ctx)
            status = Status.OK
        except CheckSkipped as skipped:
            detail, status = str(skipped), Status.SKIP
        except CheckFailed as failure:
            detail, status = redact(str(failure)), Status.FAIL
        except Exception as error:
            detail, status = f"{type(error).__name__}: {redact(str(error))[:300]}", Status.FAIL
        results.append(
            CheckResult(
                check.name,
                status,
                detail,
                check.hint if status is Status.FAIL else "",
                round((time.monotonic() - began) * 1000),
            )
        )
    return results


def render(results: Sequence[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = {"ok": " OK ", "fail": "FAIL", "skip": "skip"}[r.status.value]
        lines.append(f"[{mark}] {r.name:<20} {r.detail} ({r.millis} ms)")
        if r.hint:
            lines.append(f"       try: {r.hint}")
    failed = sum(1 for r in results if r.status is Status.FAIL)
    lines.append(f"{len(results)} checks, {failed} failed")
    return "\n".join(lines)


def build_context(settings: Any, role: str) -> Context:
    """The same wiring the API and the worker use, so a pass means that wiring works."""
    import os

    import httpx

    from nodo_api.bootstrap import azure_adapters, azure_clients, postgres_database

    clients = azure_clients(settings)
    adapters = azure_adapters(settings, clients)
    return Context(
        role=role,
        settings=settings,
        credential=clients["credential"],
        db=postgres_database(settings, clients["credential"]),
        blobs=adapters.blobs,
        blob_service=clients["blob"],
        servicebus=clients["servicebus"],
        search_index=clients["search_index"],
        retriever=adapters.retriever,
        embedder=getattr(adapters.retriever.target, "_embedder", None),
        llm=adapters.llm,
        extractor=adapters.extractor,
        http=httpx.Client(timeout=30.0),
        public_host=os.environ.get("NODO_PREFLIGHT_PUBLIC_HOST") or None,
    )
