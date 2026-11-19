"""expected_point_share rejects a scale of 0 or less (issue #134)."""

from __future__ import annotations

import math

import pytest

from open_engine import EvaluationError, expected_point_share


@pytest.mark.parametrize("scale", [0, 0.0, -0.0, -1, -2.9, -1e-300])
def test_scale_at_or_below_zero_is_rejected(scale: float) -> None:
    with pytest.raises(EvaluationError, match="^scale must be greater than 0$"):
        expected_point_share(4.0, 3.5, scale)


@pytest.mark.parametrize(
    ("scale", "message"),
    [
        ("1", "^scale must be an int or float, not str$"),
        (True, "^scale must be an int or float, not bool$"),
        (math.nan, "^scale must be finite as a float$"),
        (math.inf, "^scale must be finite as a float$"),
    ],
)
def test_scale_type_and_finiteness_checks_run_first(scale: object, message: str) -> None:
    with pytest.raises(EvaluationError, match=message):
        expected_point_share(4.0, 3.5, scale)  # type: ignore[arg-type]


def test_wrong_type_rating_is_reported_before_a_zero_scale() -> None:
    with pytest.raises(EvaluationError, match="^team_a_rating must be an int or float, not str$"):
        expected_point_share("-1", 3.5, 0)  # type: ignore[arg-type]


def test_overflowing_difference_with_positive_scale_still_raises_overflow_error() -> None:
    with pytest.raises(EvaluationError, match="^the rating difference is too large"):
        expected_point_share(10**308, -(10**308), 1)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((4.0, 3.5, 1.0), 0.7597469266479578),
        ((3.0, 4.0, 0.5), 0.009900990099009901),
        ((4, 3, 2), 0.7597469266479578),
        ((4.0, 3.5, 1e-300), 0.9999999999989999),
    ],
)
def test_positive_scale_results_are_unchanged(
    args: tuple[float, float, float], expected: float
) -> None:
    assert expected_point_share(*args) == expected
