"""Integration tests against a real PostgreSQL. They run when NODO_TEST_POSTGRES_DSN points at a
superuser connection (CI starts a service container; locally use a throwaway container)."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from nodo_api.adapters.azure.graph_pg import PgGraphStore  # noqa: E402
from nodo_api.adapters.azure.migrate import apply_migrations, grant_roles, migration_files  # noqa: E402
from nodo_api.adapters.azure.postgres import (  # noqa: E402
    PgAuditLog,
    PgJobOutbox,
    PgMeetingRepository,
    PgTranscriptRepository,
    PostgresDatabase,
)
from nodo_api.domain.models import (  # noqa: E402
    Locale,
    Meeting,
    MeetingState,
    RecordingAttestation,
    TranscriptSegment,
)
from nodo_api.errors import NotFoundError  # noqa: E402

DSN = os.environ.get("NODO_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NODO_TEST_POSTGRES_DSN is not set")

APP_ROLE = "nodo_app_test"
APP_PASSWORD = "app-test-password"
A, B = "ws-a", "ws-b"


def _app_dsn() -> str:
    params = conninfo_to_dict(DSN)
    params.update(user=APP_ROLE, password=APP_PASSWORD)
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def admin_db():
    db = PostgresDatabase.from_dsn(DSN)
    with db.admin() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
        conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
    with db.admin() as conn:
        apply_migrations(conn)
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        conn.execute(f"REVOKE UPDATE, DELETE ON audit_events FROM {APP_ROLE}")
    yield db
    db.close()


@pytest.fixture
def db(admin_db):
    """The application connection: a non-superuser, so row level security really applies."""
    database = PostgresDatabase.from_dsn(_app_dsn())
    yield database
    database.close()


def _meeting(meeting_id: str, owner: str = "alice", workspace: str = A) -> Meeting:
    now = datetime.now(UTC)
    return Meeting(
        id=meeting_id,
        workspace_id=workspace,
        owner_id=owner,
        created_at=now,
        state=MeetingState.UPLOADING,
        attestation=RecordingAttestation(
            owner, meeting_id, "2026-10-v1", now, "retention-30d", Locale.EN, "audit-1", "sig"
        ),
        project="NODO",
    )


def _segment(meeting_id: str, segment_id: str, text: str, version: int = 1) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id, meeting_id, "S1", 0, 1000, text, 0.9, version, Locale.EN, "NODO"
    )


def test_migrations_are_applied_once(admin_db) -> None:
    with admin_db.admin() as conn:
        assert apply_migrations(conn) == []
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [version for version, _, _ in migration_files()]


def test_meeting_round_trip_and_ownership(db) -> None:
    repo = PgMeetingRepository(db)
    created = repo.add(A, _meeting("m-own"), "key-own-0001", "hash-1")
    fetched = repo.get_owned(A, "alice", "m-own")
    assert fetched == created
    assert fetched.attestation.locale is Locale.EN
    with pytest.raises(NotFoundError):
        repo.get_owned(A, "mallory", "m-own")
    assert "m-own" in repo.owned_ids(A, "alice")
    assert "m-own" not in repo.owned_ids(A, "mallory")


def test_speaker_names_survive_a_save_and_unicode_round_trips(db) -> None:
    repo = PgMeetingRepository(db)
    meeting = repo.add(A, _meeting("m-names"), "key-names-01", "hash-n")
    assert meeting.speaker_names == ()
    repo.save(A, replace(meeting, speaker_names=(("S1", "Zoë"), ("S2", "Łukasz"))))
    stored = repo.get_owned(A, "alice", "m-names")
    assert stored.speaker_names == (("S1", "Zoë"), ("S2", "Łukasz"))
    repo.save(A, replace(stored, speaker_names=()))
    assert repo.get_owned(A, "alice", "m-names").speaker_names == ()


def test_idempotency_is_per_user_and_returns_the_stored_hash(db) -> None:
    repo = PgMeetingRepository(db)
    first = repo.add(A, _meeting("m-idem-a", "alice"), "shared-key-1", "hash-a")
    again = repo.add(A, _meeting("m-idem-a2", "alice"), "shared-key-1", "hash-a")
    assert again.id == first.id
    other = repo.add(A, _meeting("m-idem-b", "bob"), "shared-key-1", "hash-b")
    assert other.id == "m-idem-b"
    found, stored_hash = repo.get_by_idempotency_key(A, "alice", "shared-key-1")
    assert (found.id, stored_hash) == ("m-idem-a", "hash-a")


def test_concurrent_creates_with_one_key_make_one_meeting(db) -> None:
    repo = PgMeetingRepository(db)
    results: list[str] = []

    def create(index: int) -> None:
        meeting = repo.add(A, _meeting(f"m-race-{index}"), "race-key-0001", "hash-r")
        results.append(meeting.id)

    threads = [threading.Thread(target=create, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1


def test_a_deleted_meeting_frees_its_idempotency_key(db) -> None:
    repo = PgMeetingRepository(db)
    first = repo.add(A, _meeting("m-del-1"), "delete-key-001", "hash-d")
    repo.save(A, replace(first, tombstoned_at=datetime.now(UTC)))
    assert repo.get_by_idempotency_key(A, "alice", "delete-key-001") is None
    second = repo.add(A, _meeting("m-del-2"), "delete-key-001", "hash-d")
    assert second.id == "m-del-2"
    with pytest.raises(NotFoundError):
        repo.get_owned(A, "alice", "m-del-1")
    assert repo.get_owned(A, "alice", "m-del-1", include_tombstoned=True).tombstoned_at


def test_row_level_security_hides_other_workspaces_even_without_a_where_clause(db) -> None:
    repo = PgMeetingRepository(db)
    repo.add(A, _meeting("m-rls-a"), "rls-key-0001", "h")
    repo.add(B, _meeting("m-rls-b", workspace=B), "rls-key-0002", "h")
    with db.workspace(A) as conn:
        ids = {r["id"] for r in conn.execute("SELECT id FROM meetings")}
    assert "m-rls-a" in ids
    assert "m-rls-b" not in ids
    with db._pool.connection() as conn:  # no workspace set at all: fail closed
        assert conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 0


def test_rls_rejects_writing_into_another_workspace(db) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.workspace(A) as conn:
        conn.execute(
            "INSERT INTO meetings (workspace_id, id, owner_id, state, created_at)"
            " VALUES (%s, 'm-evil', 'alice', 'uploading', now())",
            (B,),
        )


def test_transcript_versions_are_immutable_and_latest_wins(db) -> None:
    PgMeetingRepository(db).add(A, _meeting("m-tr"), "tr-key-000001", "h")
    transcripts = PgTranscriptRepository(db)
    transcripts.add_version(A, "m-tr", (_segment("m-tr", "s1", "one"),))
    transcripts.add_version(A, "m-tr", (_segment("m-tr", "s1", "two", version=2),))
    with pytest.raises(ValueError, match="immutable"):
        transcripts.add_version(A, "m-tr", (_segment("m-tr", "s1", "again"),))
    assert transcripts.get_version(A, "m-tr", 1)[0].text == "one"
    assert transcripts.latest(A, "m-tr")[0].text == "two"
    assert transcripts.get_segment(A, "m-tr", "s1").version == 2
    assert transcripts.get_segment(A, "other-meeting", "s1") is None
    transcripts.erase(A, "m-tr")
    with pytest.raises(NotFoundError):
        transcripts.latest(A, "m-tr")


def test_audit_trail_is_append_only_for_the_application(db) -> None:
    audit = PgAuditLog(db)
    event_id = audit.append(A, "recording_attested", {"user_id": "alice"})
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.workspace(A) as conn:
        conn.execute("DELETE FROM audit_events WHERE id = %s", (event_id,))


def test_outbox_records_a_job_once_and_tracks_publication(db) -> None:
    outbox = PgJobOutbox(db)
    job_id, published = outbox.record(A, "m-job", "transcribe", {"meeting_id": "m-job"})
    assert published is False
    again_id, _ = outbox.record(A, "m-job", "transcribe", {"meeting_id": "m-job"})
    assert again_id == job_id
    outbox.mark_published(A, "m-job", "transcribe")
    assert outbox.record(A, "m-job", "transcribe", {"meeting_id": "m-job"}) == (job_id, True)


def test_granted_roles_can_write_data_but_not_edit_the_audit_trail(admin_db) -> None:
    role = "id-nodo-test-api"  # managed identity names contain hyphens and must be quoted
    with admin_db.admin() as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            conn.execute(f'DROP OWNED BY "{role}"')
            conn.execute(f'DROP ROLE "{role}"')
        conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
        database = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        grant_roles(conn, (role,), database)
        can = lambda table, action: conn.execute(  # noqa: E731
            "SELECT has_table_privilege(%s, %s, %s) AS ok", (role, table, action)
        ).fetchone()["ok"]
        assert can("meetings", "DELETE") and can("meetings", "INSERT")
        assert can("audit_events", "INSERT") and can("audit_events", "SELECT")
        assert not can("audit_events", "DELETE") and not can("audit_events", "UPDATE")
        assert not conn.execute(
            "SELECT has_schema_privilege(%s, 'public', 'CREATE') AS ok", (role,)
        ).fetchone()["ok"]


def test_the_http_api_works_end_to_end_on_postgres(db, monkeypatch) -> None:
    """The same HTTP flow the memory tests cover, with PostgreSQL as the source of truth."""
    from fastapi.testclient import TestClient

    from nodo_api import bootstrap
    from nodo_api.config import Settings
    from nodo_api.main import create_app

    memory = bootstrap.memory_adapters(
        Settings(environment="test", auth_mode="dev", attestation_hmac_key="x" * 20)
    )
    pg = bootstrap.Adapters(
        meetings=PgMeetingRepository(db),
        transcripts=PgTranscriptRepository(db),
        blobs=memory.blobs,
        jobs=memory.jobs,
        retriever=memory.retriever,
        graph=PgGraphStore(db),
        llm=memory.llm,
        audit=PgAuditLog(db),
        speech=memory.speech,
        extractor=memory.extractor,
    )
    monkeypatch.setattr(bootstrap, "build_adapters", lambda settings: pg)
    monkeypatch.setattr("nodo_api.main.build_adapters", lambda settings: pg)
    settings = Settings(
        environment="test",
        auth_mode="dev",
        dev_token="e2e-token-0123456789",
        dev_user_id="alice",
        dev_workspace_id=A,
        attestation_hmac_key="e2e-attestation-key-0123456789",
    )
    headers = {"Authorization": "Bearer e2e-token-0123456789"}
    attestation = {
        "notice_version": "2026-10-v1",
        "locale": "pl",
        "retention_policy_id": "retention-30d",
        "acknowledged": True,
    }
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/meetings",
            headers={**headers, "Idempotency-Key": "e2e-key-0001"},
            json={"recording_attestation": attestation, "project": "NODO"},
        )
        assert created.status_code == 201
        meeting_id = created.json()["id"]
        repeat = client.post(
            "/v1/meetings",
            headers={**headers, "Idempotency-Key": "e2e-key-0001"},
            json={"recording_attestation": attestation, "project": "NODO"},
        )
        assert repeat.json()["id"] == meeting_id
        assert (
            client.get(f"/v1/meetings/{meeting_id}", headers=headers).json()["state"] == "uploading"
        )
        done = client.post(f"/v1/meetings/{meeting_id}/complete", headers=headers)
        assert done.json()["state"] == "transcribing"

        PgTranscriptRepository(db).add_version(
            A, meeting_id, (_segment(meeting_id, "seg-1", "Kolejka asynchroniczna"),)
        )
        review = client.patch(
            f"/v1/meetings/{meeting_id}/review",
            headers=headers,
            json={
                "corrections": [{"segment_id": "seg-1", "text": "Kolejka async"}],
                "base_version": 1,
            },
        )
        assert review.json()["version"] == 2
        page = client.get(f"/v1/meetings/{meeting_id}/transcript", headers=headers).json()
        assert page["items"][0]["text"] == "Kolejka async" and page["total"] == 1

        assert client.delete(f"/v1/meetings/{meeting_id}", headers=headers).status_code == 204
        assert client.get(f"/v1/meetings/{meeting_id}", headers=headers).status_code == 404
        assert client.delete(f"/v1/meetings/{meeting_id}", headers=headers).status_code == 204


from tests.graph_contract import GraphContract  # noqa: E402


class TestPgGraph(GraphContract):
    """The same behaviour tests the in-memory graph passes, on real PostgreSQL."""

    @pytest.fixture
    def store(self, db):
        return PgGraphStore(db)

    @pytest.fixture
    def ws(self):
        return f"ws-{uuid.uuid4().hex[:8]}"
