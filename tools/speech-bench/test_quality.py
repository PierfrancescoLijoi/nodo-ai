from quality import (
    TranscriptObservation,
    bootstrap_interval,
    character_error_rate,
    entity_recall,
    nearest_rank_percentile,
    number_recall,
    observation_from_result,
    summarize,
)


def observation(**overrides) -> TranscriptObservation:
    values = {
        "case_id": "clean-en-1",
        "locale": "en",
        "scenario": "clean",
        "reference": "Marco approved Azure Service Bus on 14 November.",
        "hypothesis": "Marco approved Azure Service Bus on 14 November.",
        "audio_seconds": 60.0,
        "wall_seconds": 6.0,
        "reference_speakers": (("A", 0.0, 30.0), ("B", 30.0, 60.0)),
        "hypothesis_speakers": (("S1", 0.0, 30.0), ("S2", 30.0, 60.0)),
    }
    values.update(overrides)
    return TranscriptObservation(**values)


def test_character_error_rate_is_normalized_and_handles_empty_text() -> None:
    assert character_error_rate("Café", "cafe") == 0.25
    assert character_error_rate("", "") == 0.0
    assert character_error_rate("", "unexpected") == 1.0


def test_entities_and_numbers_are_scored_separately_from_wer() -> None:
    reference = "Marco approved Azure Service Bus on 14 November for €10."
    hypothesis = "Marco approved Service Bus on November for ten euros."
    assert entity_recall(reference, hypothesis) == 0.5
    assert number_recall(reference, hypothesis) == 0.0


def test_percentile_uses_nearest_rank_for_small_reliable_samples() -> None:
    samples = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert nearest_rank_percentile(samples, 0.5) == 3.0
    assert nearest_rank_percentile(samples, 0.95) == 100.0


def test_bootstrap_interval_is_reproducible_and_contains_the_mean() -> None:
    low, high = bootstrap_interval([0.1, 0.2, 0.3, 0.4], seed=7, resamples=500)
    assert low <= 0.25 <= high
    assert (low, high) == bootstrap_interval(
        [0.1, 0.2, 0.3, 0.4], seed=7, resamples=500
    )


def test_summary_reports_micro_quality_latency_and_speaker_metrics() -> None:
    rows = [
        observation(),
        observation(
            case_id="noise-en-1",
            scenario="snr10",
            hypothesis="Marco rejected Azure Service Bus.",
            wall_seconds=12.0,
            hypothesis_speakers=(("S1", 0.0, 60.0),),
        ),
    ]
    result = summarize(rows, bootstrap_resamples=200)

    assert result["samples"] == 2
    assert result["audio_hours"] == round(120 / 3600, 4)
    assert result["latency_seconds"]["p95"] == 12.0
    assert result["rtf"]["p95"] == 0.2
    assert 0.0 < result["wer"]["micro"] < 1.0
    assert result["speaker_accuracy"]["mean"] == 0.75
    assert {item["scenario"] for item in result["by_scenario"]} == {"clean", "snr10"}


def test_summary_gate_requires_enough_samples_before_claiming_success() -> None:
    result = summarize([observation()], min_samples=3, bootstrap_resamples=50)
    assert result["gate"]["status"] == "insufficient_data"
    assert result["gate"]["sample_shortfall"] == 2


def test_provider_result_is_converted_to_a_typed_observation() -> None:
    row = observation_from_result(
        {
            "file": "en-snr10",
            "locale": "en",
            "scenario": "snr10",
            "reference": "hello world",
            "hypothesis": "hello",
            "audio_s": 10,
            "wall_s": 2,
            "reference_speakers": [["A", 0, 10]],
            "hypothesis_speakers": [["S1", 0, 10]],
        }
    )
    assert row.case_id == "en-snr10"
    assert row.reference_speakers == (("A", 0.0, 10.0),)


def test_provider_result_rejects_non_positive_durations() -> None:
    try:
        observation_from_result(
            {
                "file": "bad",
                "locale": "en",
                "scenario": "clean",
                "reference": "a",
                "hypothesis": "a",
                "audio_s": 0,
                "wall_s": 1,
            }
        )
    except ValueError as error:
        assert "audio_s" in str(error)
    else:
        raise AssertionError("zero-duration observations must be rejected")
