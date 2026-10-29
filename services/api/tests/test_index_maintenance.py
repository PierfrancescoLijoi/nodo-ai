"""Search index and graph kept honest against the store (ADR-007: derived data is rebuildable).

verify reports what is wrong and changes nothing; repair rebuilds what is missing or stale, removes
what should be gone and asks for a re-analysis where the graph points at text that does not exist.
One meeting failing never stops the run."""

from __future__ import annotations

import pytest

from nodo_api.adapters.memory import MemoryJobQueue, MemoryMaintenanceStore, MemorySpeech
from nodo_api.adapters.memory.extractor import KeywordExtractor
from nodo_api.domain.graph import EntityType, MeetingRef
from nodo_api.domain.maintenance import FindingKind
from nodo_api.domain.models import MeetingState
from nodo_api.main import AppContainer
from nodo_api.services.index_maintenance import IndexMaintenanceService
from nodo_api.services.ingestion import IngestionService
from tests.graph_contract import ent, rel

WORKSPACE, USER = "workspace-a", "user-a"
LOCALES = ("en-GB",)


class Rig:
    def __init__(self, app, attestation) -> None:
        self.container: AppContainer = app.state.container
        self.attestation = attestation
        self.jobs = MemoryJobQueue()
        self.ingestion = IngestionService(
            self.container.meetings,
            self.container.transcripts,
            self.container.blobs,
            self.container.retriever,
            MemorySpeech(),
            LOCALES,
            KeywordExtractor(),
            self.container.graph,
        )
        self.service = IndexMaintenanceService(
            MemoryMaintenanceStore(self.container.meetings),
            self.container.transcripts,
            self.container.retriever,
            self.container.graph,
            self.jobs,
        )

    def meeting(self, key: str, *, process: bool = True) -> str:
        created = self.container.meeting_service.create(WORKSPACE, USER, key, self.attestation)
        self.container.meeting_service.complete(WORKSPACE, USER, created.id)
        if process:
            self.ingestion.process(WORKSPACE, created.id)
        return created.id

    def indexed(self, meeting_id: str) -> int:
        return self.container.retriever.count(WORKSPACE, meeting_id)

    def segments(self, meeting_id: str):
        return self.container.transcripts.latest(WORKSPACE, meeting_id)


def kinds(report) -> set[tuple[FindingKind, str]]:
    return {(f.kind, f.meeting_id) for f in report.findings}


def test_a_healthy_system_verifies_clean(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-1")
    report = rig.service.verify()
    assert report.clean and report.findings == []
    assert report.checked >= 1 and report.repaired == 0
    assert rig.indexed(mine) == len(rig.segments(mine))


def test_a_meeting_missing_from_the_index_is_found_and_verify_changes_nothing(
    app, attestation
) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-2")
    rig.container.retriever.erase(WORKSPACE, mine)
    report = rig.service.verify()
    assert (FindingKind.MISSING_FROM_INDEX, mine) in kinds(report) and not report.clean
    assert rig.indexed(mine) == 0  # verify is read-only


def test_repair_rebuilds_a_missing_index_and_the_next_verify_is_clean(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-3")
    rig.container.retriever.erase(WORKSPACE, mine)
    report = rig.service.repair()
    assert report.repaired >= 1 and report.failed == []
    assert rig.indexed(mine) == len(rig.segments(mine))
    assert rig.service.verify().clean
    assert rig.container.retriever.search(WORKSPACE, USER, "ingestion queue", None, 10)


def test_a_partly_indexed_meeting_is_completed(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-4")
    rig.container.retriever._segments.pop((WORKSPACE, mine, "seg-00002"))
    assert (FindingKind.PARTIAL_INDEX, mine) in kinds(rig.service.verify())
    rig.service.repair()
    assert rig.indexed(mine) == len(rig.segments(mine))


def test_documents_that_no_longer_belong_to_the_transcript_are_removed(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-5")
    extra = rig.segments(mine)[0]
    from dataclasses import replace

    rig.container.retriever.index(WORKSPACE, USER, [replace(extra, id="seg-00099", text="old")])
    assert (FindingKind.STALE_INDEX, mine) in kinds(rig.service.verify())
    rig.service.repair()
    assert rig.indexed(mine) == len(rig.segments(mine))
    found = rig.container.retriever.search(WORKSPACE, USER, "old", None, 10)
    assert all(s.id != "seg-00099" for s in found)


def test_what_should_be_gone_after_a_delete_is_removed_everywhere(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-6")
    segments = rig.segments(mine)
    rig.container.meeting_service.delete(WORKSPACE, USER, mine)
    # a failed cascade: the derived data came back or was never removed
    rig.container.retriever.index(WORKSPACE, USER, segments)
    ref = MeetingRef(mine, "Gone", rig.container.meetings._meetings[(WORKSPACE, mine)].created_at)
    from nodo_api.domain.graph import Extraction

    rig.container.graph.upsert_extraction(
        WORKSPACE,
        USER,
        ref,
        Extraction(
            entities=(ent("p", EntityType.PERSON, "Sara", segments[0].id),),
            relations=(),
            summary="",
        ),
    )
    found = kinds(rig.service.verify())
    assert {(FindingKind.LEFTOVER_INDEX, mine), (FindingKind.LEFTOVER_GRAPH, mine)} <= found
    rig.service.repair()
    assert rig.indexed(mine) == 0
    assert rig.container.graph.evidence_segment_ids(WORKSPACE, USER, mine) == frozenset()
    assert rig.service.verify().clean


def test_graph_evidence_pointing_at_missing_text_asks_for_a_new_analysis(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-7")
    ref = MeetingRef(mine, "T", rig.container.meetings._meetings[(WORKSPACE, mine)].created_at)
    from nodo_api.domain.graph import Extraction, RelationKind

    rig.container.graph.upsert_extraction(
        WORKSPACE,
        USER,
        ref,
        Extraction(
            entities=(
                ent("a", EntityType.PERSON, "Sara", "seg-00099"),
                ent("b", EntityType.TOPIC, "Retries", "seg-00099"),
            ),
            relations=(rel("a", RelationKind.ABOUT, "b", "seg-00099"),),
            summary="",
        ),
    )
    assert (FindingKind.DANGLING_EVIDENCE, mine) in kinds(rig.service.verify())
    assert rig.jobs.scheduled == []
    rig.service.repair()
    rig.service.repair()  # the second run on the same day asks for nothing more
    asked = [k for k in rig.jobs.job_keys() if k[2] == "reanalyze"]
    assert len(asked) == 1 and asked[0][1].startswith(f"{mine}:repair-graph:")


def test_a_meeting_still_being_transcribed_is_left_alone(app, attestation) -> None:
    rig = Rig(app, attestation)
    waiting = rig.meeting("idx-8", process=False)
    report = rig.service.repair()
    assert report.skipped >= 1 and report.findings == []
    assert (
        rig.container.meetings.get_owned(WORKSPACE, USER, waiting).state
        is MeetingState.TRANSCRIBING
    )


def test_one_failing_meeting_does_not_stop_the_others(app, attestation) -> None:
    rig = Rig(app, attestation)
    good, bad = rig.meeting("idx-9"), rig.meeting("idx-10")
    for meeting_id in (good, bad):
        rig.container.retriever.erase(WORKSPACE, meeting_id)
    real_index = rig.container.retriever.index

    def flaky(workspace_id, owner_id, segments):
        if segments and segments[0].meeting_id == bad:
            raise ConnectionError("search unavailable")
        return real_index(workspace_id, owner_id, segments)

    rig.container.retriever.index = flaky
    report = rig.service.repair()
    assert rig.indexed(good) > 0 and rig.indexed(bad) == 0
    assert [(f.meeting_id, f.error) for f in report.failed] == [(bad, "ConnectionError")]
    assert not report.clean


@pytest.mark.parametrize("batch", [1, 2, 100])
def test_paging_covers_every_meeting_whatever_the_batch_size(app, attestation, batch) -> None:
    rig = Rig(app, attestation)
    made = [rig.meeting(f"page-{batch}-{n}") for n in range(3)]
    for meeting_id in made:
        rig.container.retriever.erase(WORKSPACE, meeting_id)
    report = rig.service.repair(batch=batch)
    assert all(rig.indexed(m) > 0 for m in made)
    assert report.repaired >= 3


def test_the_audit_trail_names_the_meeting_and_the_finding_never_the_text(app, attestation) -> None:
    rig = Rig(app, attestation)
    mine = rig.meeting("idx-11")
    rig.container.retriever.erase(WORKSPACE, mine)
    log = []
    audit = rig.container.audit
    original = audit.append
    audit.append = lambda ws, kind, meta: (log.append((kind, meta)), original(ws, kind, meta))[1]
    rig.service._audit = audit
    rig.service.repair()
    assert ("index_repaired", {"meeting_id": mine, "finding": "missing_from_index"}) in log


def test_the_admin_commands_run_on_a_local_archive_and_refuse_other_backends(tmp_path) -> None:
    from nodo_api import admin
    from nodo_api.config import Settings

    local = Settings(
        environment="prod",
        backend="local",
        auth_mode="local",
        local_token="local-test-token-0123456789-0123456789",
        local_data_dir=tmp_path,
        attestation_hmac_key="local-test-attestation-key-0123456789",
    )
    memory = Settings(
        environment="test",
        auth_mode="dev",
        dev_token="test-dev-token-0123456789",
        attestation_hmac_key="test-only-attestation-key-0123456789",
    )
    admin.run("verify-index", local)  # an empty archive is clean: no exit
    with pytest.raises(SystemExit):
        admin.run("verify-index", memory)
    assert "verify-index" in admin.COMMANDS and "reindex" in admin.COMMANDS


def test_entities_that_cannot_be_found_by_meaning_are_found_and_indexed(app, attestation) -> None:
    from nodo_api.services.entity_index import EntityIndexer

    rig = Rig(app, attestation)
    rig.meeting("idx-ent")
    graph = rig.container.graph

    class Meaning:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    indexer = EntityIndexer(graph, Meaning(), dimensions=3)
    service = IndexMaintenanceService(
        MemoryMaintenanceStore(rig.container.meetings),
        rig.container.transcripts,
        rig.container.retriever,
        graph,
        rig.jobs,
        entity_indexer=indexer,
    )
    found = service.verify()
    assert any(f.kind is FindingKind.ENTITY_VECTORS_MISSING for f in found.findings)
    assert (
        sum(f.kind is FindingKind.ENTITY_VECTORS_MISSING for f in found.findings) == 1
    )  # per owner
    service.repair()
    assert service.verify().clean
