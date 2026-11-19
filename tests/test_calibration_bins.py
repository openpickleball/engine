"""Calibration bins in the evaluation report partition the scored pairs (issue #130)."""

import math
from datetime import date

import pytest

import open_engine._evaluation as evaluation_module
from open_engine import Config, PrequentialPrediction

EDGES = [k / 10 for k in range(11)]
NEIGHBORS = [
    value
    for edge in EDGES
    for value in (
        math.nextafter(edge, -math.inf),
        edge,
        math.nextafter(edge, math.inf),
        edge - 1e-5,
        edge + 1e-5,
    )
    if 0.0 <= value <= 1.0
]


def _prediction(index: int, expected: float) -> PrequentialPrediction:
    return PrequentialPrediction(
        match_id=f"m{index}",
        played_on=date(2025, 1, 6),
        players=("pa", "pb", "pc", "pd"),
        expected=expected,
        actual=0.5,
        team_a_won=True,
        win_probability_raw=0.5,
        win_probability=0.5,
    )


def _expected_bin(expected: float) -> float:
    """The lower edge k / 10 of the one bin with k / 10 <= e < (k + 1) / 10 (1.0 in bin 9)."""
    scored = round(expected, 5)  # the report scores expected to 5 places
    if scored == 1.0:
        return 9 / 10
    matches = [k / 10 for k in range(10) if k / 10 <= scored < (k + 1) / 10]
    assert len(matches) == 1
    return matches[0]


def _report(values: list[float]) -> evaluation_module.EvaluationReport:
    preds = [_prediction(i, v) for i, v in enumerate(values)]
    return evaluation_module._report_from_predictions(preds, Config(), None)[0]


def test_prediction_at_point_three_is_in_one_bin() -> None:
    report = _report([0.3])
    assert report.calibration == ((0.3, 0.3, 0.5, 1),)


@pytest.mark.parametrize("expected", NEIGHBORS)
def test_each_prediction_lands_in_exactly_one_bin(expected: float) -> None:
    report = _report([expected])
    assert [(row[0], row[3]) for row in report.calibration] == [(_expected_bin(expected), 1)]


def test_bin_counts_add_up_to_matches_scored() -> None:
    report = _report(NEIGHBORS)
    assert sum(row[3] for row in report.calibration) == report.matches_scored == len(NEIGHBORS)
