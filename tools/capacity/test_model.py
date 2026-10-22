from dataclasses import replace

import pytest
from model import Assumptions, markdown, model, what_if_float32_vectors


def test_everything_that_scales_with_users_grows_with_them() -> None:
    small, large = model(1_000), model(100_000)
    for field in ("api_peak_rps", "chat_tpm_peak", "extraction_tpm_peak", "pg_tb_12m", "search_gb"):
        assert getattr(large, field) > getattr(small, field) * 50


def test_the_api_never_runs_below_one_replica_per_zone() -> None:
    assert model(10).api_replicas == 3


def test_replicas_follow_the_peak_request_rate() -> None:
    result = model(100_000)
    capacity = result.api_replicas * Assumptions().api_rps_per_vcpu * Assumptions().api_vcpu_per_replica
    assert capacity >= result.api_peak_rps


def test_staging_the_jobs_needs_far_fewer_workers_than_waiting_in_one() -> None:
    result = model(100_000)
    assert result.executions_staged * 10 < result.executions_long_polling


def test_a_job_that_waits_for_speech_costs_in_proportion_to_the_wait() -> None:
    patient = replace(Assumptions(), speech_wait_hours_per_audio_hour=1.0)
    assert model(10_000, patient).executions_long_polling > model(10_000).executions_long_polling * 3


def test_full_precision_vectors_would_multiply_the_index_by_twelve() -> None:
    ratio = what_if_float32_vectors(10_000) / model(10_000).search_gb
    assert ratio == pytest.approx(12.0)


def test_a_search_partition_count_covers_the_index() -> None:
    a = Assumptions()
    result = model(100_000)
    assert result.search_partitions * a.search_partition_gb * a.search_partition_fill >= result.search_gb
    assert result.search_replicas >= 3


def test_the_table_has_one_column_per_size() -> None:
    table = markdown([1_000, 10_000])
    assert table.splitlines()[0].count("users") == 2
