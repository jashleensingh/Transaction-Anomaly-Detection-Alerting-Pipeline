import pytest

from anomaly_pipeline.stats import modified_z_score, upper_fence


def test_typical_value_has_small_z():
    history = [40, 45, 50, 55, 60, 48, 52]
    assert abs(modified_z_score(50, history)) < 1


def test_large_value_has_large_positive_z():
    history = [40, 45, 50, 55, 60, 48, 52]
    assert modified_z_score(900, history) > 6


def test_small_value_has_negative_z():
    history = [40, 45, 50, 55, 60, 48, 52]
    assert modified_z_score(5, history) < 0


def test_identical_history_does_not_explode():
    history = [50.0] * 10
    assert modified_z_score(50.0, history) == pytest.approx(0)
    assert modified_z_score(52.0, history) < 1  # floor on spread keeps small changes calm
    assert modified_z_score(500.0, history) > 6


def test_empty_history_raises():
    with pytest.raises(ValueError):
        modified_z_score(10, [])


def test_upper_fence():
    values = list(range(1, 101))
    # inclusive quartiles of 1..100 are Q1=25.75 and Q3=75.25, so IQR=49.5
    assert upper_fence(values, 3.0) == pytest.approx(75.25 + 3 * 49.5)
