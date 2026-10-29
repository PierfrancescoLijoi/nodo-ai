from eval.score_results import score_predictions


def test_scores_retrieval_answerability_and_forbidden_leaks() -> None:
    cases = [
        {
            "id": "supported",
            "category": "single_meeting_fact",
            "expected_answerability": "supported",
            "expected_citation_segment_ids": ["s1", "s2"],
            "forbidden_segment_ids": ["secret"],
        },
        {
            "id": "blocked",
            "category": "acl_negative",
            "expected_answerability": "unanswerable",
            "expected_citation_segment_ids": [],
            "forbidden_segment_ids": ["other-workspace"],
        },
    ]
    predictions = [
        {
            "id": "supported",
            "answerability": "supported",
            "citation_segment_ids": ["s1", "irrelevant"],
            "latency_ms": 120,
        },
        {
            "id": "blocked",
            "answerability": "supported",
            "citation_segment_ids": ["other-workspace"],
            "latency_ms": 200,
        },
    ]

    result = score_predictions(cases, predictions)

    assert result["coverage"] == 1.0
    assert result["citation_precision"] == 1 / 3
    assert result["citation_recall"] == 0.5
    assert result["answerability_accuracy"] == 0.5
    assert result["forbidden_leak_count"] == 1
    assert result["latency_ms"]["p95"] == 200


def test_missing_predictions_are_reported_and_do_not_disappear() -> None:
    cases = [
        {
            "id": "a",
            "category": "unanswerable",
            "expected_answerability": "unanswerable",
            "expected_citation_segment_ids": [],
            "forbidden_segment_ids": [],
        },
        {
            "id": "b",
            "category": "deleted_data",
            "expected_answerability": "unanswerable",
            "expected_citation_segment_ids": [],
            "forbidden_segment_ids": ["deleted"],
        },
    ]

    result = score_predictions(
        cases,
        [
            {
                "id": "a",
                "answerability": "unanswerable",
                "citation_segment_ids": [],
                "latency_ms": 1,
            }
        ],
    )

    assert result["coverage"] == 0.5
    assert result["missing_case_ids"] == ["b"]
    assert result["gate"]["status"] == "fail"
