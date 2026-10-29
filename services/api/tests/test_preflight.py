"""The first connection to Azure, checked before anything depends on it (ADR-023).

Each check exercises the same code path production uses and says, when it fails, what is probably
wrong and where to look. The checks run against fakes here; against Azure they are what turns
"deployed" into "working"."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from nodo_api.adapters.azure.openai_extractor import EXTRACTION_SCHEMA
from nodo_api.adapters.azure.openai_llm import ANSWER_SCHEMA
from nodo_api.preflight import (
    CHECKS,
    Context,
    Status,
    redact,
    render,
    run_checks,
)


# --- strict structured outputs: the rules the service enforces, checked here before it does -------
def _strict_problems(schema: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            if schema.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            properties = set(schema.get("properties", {}))
            if set(schema.get("required", [])) != properties:
                problems.append(f"{path}: every property must be required")
        for banned in ("minLength", "maxLength", "pattern", "format", "minItems", "maxItems"):
            if banned in schema:
                problems.append(f"{path}: {banned} is not supported in strict mode")
        for key, value in schema.items():
            problems += _strict_problems(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            problems += _strict_problems(value, f"{path}[{index}]")
    return problems


@pytest.mark.parametrize("schema", [ANSWER_SCHEMA, EXTRACTION_SCHEMA], ids=["answer", "extraction"])
def test_the_model_schemas_obey_the_strict_structured_output_rules(schema) -> None:
    assert _strict_problems(schema) == []


def test_the_strict_rule_checker_does_catch_a_violation() -> None:
    bad = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    assert len(_strict_problems(bad)) == 2


# --- the checks ---------------------------------------------------------------------------------
class Rows:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, answers: dict[str, Rows]) -> None:
        self.answers = answers

    def execute(self, sql: str, params=None):
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return rows
        return Rows()


class FakeDb:
    def __init__(self, answers: dict[str, Rows]) -> None:
        self.answers = answers

    def admin(self):
        return _Ctx(FakeConn(self.answers))

    def workspace(self, workspace_id: str):
        return _Ctx(FakeConn(self.answers))


class _Ctx:
    def __init__(self, value) -> None:
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc) -> bool:
        return False


def healthy_db(user="id-nodo-dev-api") -> FakeDb:
    from nodo_api.adapters.azure.migrate import migration_files

    versions = [Rows_row for Rows_row in ({"version": v} for v, _, _ in migration_files())]
    return FakeDb(
        {
            "current_user": Rows({"u": user, "v": "PostgreSQL 16.4"}),
            "schema_migrations": Rows(*versions),
            "pg_extension": Rows({"extname": "vector"}, {"extname": "pg_trgm"}),
            "relforcerowsecurity": Rows(),
            "FROM meetings": Rows({"n": 0}),
        }
    )


@dataclass
class FakeCredential:
    fail: set[str] = field(default_factory=set)

    def get_token(self, scope: str):
        if any(name in scope for name in self.fail):
            raise PermissionError("no token")
        return SimpleNamespace(token="t")


def context(**over) -> Context:
    settings = SimpleNamespace(
        azure_postgres_user="id-nodo-dev-api",
        openai_embedding_dimensions=3,
        search_index_name="segments",
        azure_servicebus_queue="ingest",
        azure_storage_account="stnodo",
        azure_speech_endpoint="https://speech.test",
        openai_chat_deployment="chat",
    )
    base = {
        "role": "api",
        "settings": settings,
        "credential": FakeCredential(),
        "db": healthy_db(),
    }
    return Context(**{**base, **over})


def result(ctx: Context, name: str):
    return run_checks(ctx, only=[name])[0]


def test_every_check_has_a_name_a_hint_and_at_least_one_role() -> None:
    names = [c.name for c in CHECKS]
    assert len(names) == len(set(names)) and len(names) >= 10
    assert all(c.hint and c.roles for c in CHECKS)


def test_tokens_are_requested_for_every_service_and_the_missing_ones_are_named() -> None:
    assert result(context(), "identity").status is Status.OK
    failed = result(context(credential=FakeCredential(fail={"servicebus", "search"})), "identity")
    assert failed.status is Status.FAIL
    assert "servicebus" in failed.detail and "search" in failed.detail


def test_the_database_login_must_be_the_expected_identity() -> None:
    assert result(context(), "postgres_connect").status is Status.OK
    wrong = result(context(db=healthy_db(user="someone-else")), "postgres_connect")
    assert wrong.status is Status.FAIL and "someone-else" in wrong.detail


def test_every_migration_must_have_run_and_the_extensions_must_exist() -> None:
    assert result(context(), "postgres_schema").status is Status.OK
    behind = healthy_db()
    behind.answers["schema_migrations"] = Rows({"version": 1})
    assert "not applied" in result(context(db=behind), "postgres_schema").detail
    no_vector = healthy_db()
    no_vector.answers["pg_extension"] = Rows({"extname": "pg_trgm"})
    assert "vector" in result(context(db=no_vector), "postgres_schema").detail


def test_a_table_with_workspace_data_but_no_forced_row_security_fails_the_isolation_check() -> None:
    assert result(context(), "postgres_isolation").status is Status.OK
    open_table = healthy_db()
    open_table.answers["relforcerowsecurity"] = Rows({"relname": "exports"})
    failed = result(context(db=open_table), "postgres_isolation")
    assert failed.status is Status.FAIL and "exports" in failed.detail


def test_an_embedding_of_the_wrong_size_is_caught_before_the_index_rejects_it() -> None:
    good = SimpleNamespace(embed=lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    bad = SimpleNamespace(embed=lambda texts: [[0.1] * 5 for _ in texts])
    assert result(context(embedder=good), "embedding").status is Status.OK
    failed = result(context(embedder=bad), "embedding")
    assert failed.status is Status.FAIL and "3" in failed.detail and "5" in failed.detail


def test_a_check_that_does_not_apply_to_this_identity_is_skipped_not_failed() -> None:
    assert result(context(role="api"), "speech").status is Status.SKIP
    assert result(context(role="worker"), "servicebus_send").status is Status.SKIP


def test_a_check_that_raises_is_reported_with_its_hint_and_never_stops_the_run() -> None:
    def boom(*_a, **_k):
        raise ConnectionError("https://x.blob.core.windows.net/audio/b?sig=SECRET&se=1")

    ctx = context(embedder=SimpleNamespace(embed=boom))
    results = run_checks(ctx)
    failed = next(r for r in results if r.name == "embedding")
    assert failed.status is Status.FAIL and "ConnectionError" in failed.detail
    assert "SECRET" not in failed.detail and "sig=***" in failed.detail
    assert len(results) == len([c for c in CHECKS])  # the others still ran


def test_the_report_names_every_failure_with_what_to_try() -> None:
    ctx = context(credential=FakeCredential(fail={"storage"}))
    text = render(run_checks(ctx, only=["identity"]))
    assert "FAIL" in text and "identity" in text and "try:" in text


def test_redact_hides_signatures_and_tokens() -> None:
    assert redact("GET https://a/b?sv=1&sig=abc%2Bdef&se=2") == "GET https://a/b?sv=1&sig=***&se=2"
    assert "Bearer ***" in redact("Authorization: Bearer eyJhbGciOi.aaa.bbb")
    assert json.loads(json.dumps({"detail": redact("sig=abc")}))["detail"] == "sig=***"
