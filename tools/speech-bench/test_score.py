from score import speaker_accuracy, speaker_count_error

REF = [("A", 0, 10), ("B", 10, 20), ("A", 20, 30)]


def test_perfect_attribution_ignores_the_names_of_the_labels() -> None:
    hyp = [("S2", 0, 10), ("S1", 10, 20), ("S2", 20, 30)]
    assert speaker_accuracy(REF, hyp) == 1.0


def test_one_wrong_turn_costs_its_share_of_the_time() -> None:
    hyp = [("S1", 0, 10), ("S2", 10, 20), ("S2", 20, 30)]
    assert round(speaker_accuracy(REF, hyp), 3) == round(20 / 30, 3)


def test_merging_everyone_into_one_speaker_gets_the_larger_share() -> None:
    hyp = [("S1", 0, 30)]
    assert round(speaker_accuracy(REF, hyp), 3) == round(20 / 30, 3)
    assert speaker_count_error(REF, hyp) == -1


def test_silence_and_missed_speech_count_against_the_score() -> None:
    hyp = [("S1", 0, 10), ("S2", 10, 15)]
    assert round(speaker_accuracy(REF, hyp), 3) == round(15 / 30, 3)


def test_splitting_one_speaker_in_two_loses_the_smaller_part() -> None:
    hyp = [("S1", 0, 10), ("S2", 10, 20), ("S3", 20, 30)]
    assert round(speaker_accuracy(REF, hyp), 3) == round(20 / 30, 3)
    assert speaker_count_error(REF, hyp) == 1


def test_no_reference_or_no_hypothesis_scores_zero() -> None:
    assert speaker_accuracy([], [("S1", 0, 1)]) == 0.0
    assert speaker_accuracy(REF, []) == 0.0
