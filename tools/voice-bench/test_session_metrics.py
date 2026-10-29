from session_metrics import SessionObservation, summarize_sessions


def session(**overrides) -> SessionObservation:
    values = {
        "session_id": "s1",
        "expected_frames": 1000,
        "received_frames": 998,
        "recovered_frames": 2,
        "duplicate_frames": 1,
        "partial_latencies_ms": (300.0, 500.0),
        "final_latencies_ms": (900.0,),
        "first_audio_latencies_ms": (1200.0,),
        "barge_in_stop_latencies_ms": (150.0,),
        "reconnect_latencies_ms": (2200.0,),
        "success": True,
    }
    values.update(overrides)
    return SessionObservation(**values)


def test_summary_measures_end_to_end_voice_slos() -> None:
    report = summarize_sessions([session(), session(session_id="s2")], min_sessions=2)

    assert report["audio_continuity"] == 1.0
    assert report["duplicate_frame_rate"] == 0.001
    assert report["partial_latency_ms"]["p95"] == 500.0
    assert report["partial_latency_ms"]["p99"] == 500.0
    assert report["barge_in_stop_ms"]["p95"] == 150.0
    assert report["session_success_rate"] == 1.0
    assert report["gate"]["status"] == "pass"


def test_missing_frames_and_slow_barge_in_fail_the_gate() -> None:
    report = summarize_sessions(
        [
            session(
                expected_frames=1000,
                received_frames=950,
                recovered_frames=0,
                barge_in_stop_latencies_ms=(450.0,),
                success=False,
            )
        ],
        min_sessions=1,
    )

    assert report["audio_continuity"] == 0.95
    assert report["gate"]["status"] == "fail"
    assert set(report["gate"]["failed_metrics"]) >= {
        "audio_continuity",
        "barge_in_stop_p95",
        "session_success_rate",
    }


def test_too_few_sessions_is_not_a_pass() -> None:
    report = summarize_sessions([session()], min_sessions=100)
    assert report["gate"]["status"] == "insufficient_data"
    assert report["gate"]["session_shortfall"] == 99


def test_invalid_frame_accounting_is_rejected() -> None:
    try:
        summarize_sessions([session(expected_frames=0)], min_sessions=1)
    except ValueError as error:
        assert "expected_frames" in str(error)
    else:
        raise AssertionError("an empty session must not be benchmarked")

    try:
        summarize_sessions(
            [session(expected_frames=100, received_frames=99, recovered_frames=2)],
            min_sessions=1,
        )
    except ValueError as error:
        assert "unique delivered frames" in str(error)
    else:
        raise AssertionError("recovered frames must not hide invalid accounting")
