import numpy as np
from stress_corpus import (
    add_white_noise,
    apply_clipping,
    apply_dropouts,
    apply_echo,
    build_cases,
    measured_snr_db,
)


def sine(seconds: float = 2.0, rate: int = 16_000) -> np.ndarray:
    timeline = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (0.2 * np.sin(2 * np.pi * 220 * timeline)).astype(np.float32)


def test_noise_case_has_requested_snr_and_is_reproducible() -> None:
    source = sine()
    first = add_white_noise(source, 10, seed=11)
    second = add_white_noise(source, 10, seed=11)
    assert np.array_equal(first, second)
    assert abs(measured_snr_db(source, first) - 10) < 0.15


def test_echo_preserves_length_and_adds_a_delayed_copy() -> None:
    source = np.zeros(16_000, dtype=np.float32)
    source[0] = 0.5
    result = apply_echo(source, rate=16_000, delay_ms=100, decay=0.4)
    assert len(result) == len(source)
    assert result[0] == 0.5
    assert result[1600] == 0.2


def test_clipping_caps_peak_and_dropout_is_seeded() -> None:
    source = sine()
    clipped = apply_clipping(source, threshold=0.1)
    assert float(np.max(np.abs(clipped))) <= 0.100001
    assert np.array_equal(
        apply_dropouts(source, 16_000, seed=3),
        apply_dropouts(source, 16_000, seed=3),
    )
    assert np.count_nonzero(apply_dropouts(source, 16_000, seed=3) == 0) > 0


def test_build_cases_covers_expected_meeting_failure_modes() -> None:
    cases = build_cases(sine(), rate=16_000, interfering=sine(3.0), seed=5)
    assert set(cases) == {
        "clean",
        "snr20",
        "snr10",
        "snr5",
        "echo",
        "clipping",
        "dropouts",
        "telephone",
        "babble",
    }
    assert all(signal.dtype == np.float32 for signal in cases.values())
    assert all(len(signal) == 32_000 for signal in cases.values())
