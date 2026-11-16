"""MatchResult.imbalance comes from the unrounded changes, with no negative zero (issue #14)."""

from __future__ import annotations

import math
from datetime import date

import pytest

from open_engine import Config, Match, RatingEngine, RatingFormat

D = RatingFormat.DOUBLES
ON = date(2026, 10, 29)


def _record(states: dict[str, tuple[float, float]], games: tuple[tuple[int, int], ...]) -> float:
    """Record a, b vs c, d with the given (rating, rd) and return the imbalance."""
    eng = RatingEngine(Config(max_doubles_partner_gap=None))
    for pid, (rating, rd) in states.items():
        state = eng.seed_provisional_rating(pid, D)
        state.rating = rating
        state.rd = rd
        state.rd_as_of = ON
    result = eng.record(Match("m", ("a", "b"), ("c", "d"), games, ON, D, best_of=1))
    assert result.counted
    return result.imbalance


@pytest.mark.parametrize(
    ("a_rating", "games"),
    [
        pytest.param(4.0, ((11, 5),), id="equal-ratings-11-5"),
        pytest.param(4.1, ((11, 1),), id="a-higher-11-1"),
        pytest.param(4.1, ((11, 2),), id="a-higher-11-2"),
    ],
)
def test_equal_mean_k_has_no_rounding_residue(
    a_rating: float, games: tuple[tuple[int, int], ...]
) -> None:
    # K of a and b average to the K of c and d, so the net is zero.
    states = {"a": (a_rating, 0.15), "b": (4.0, 0.75), "c": (4.0, 0.45), "d": (4.0, 0.45)}
    assert _record(states, games) == 0.0


def test_equal_k_split_rounding_has_no_residue() -> None:
    states = {"a": (5.2, 0.8), "b": (3.6, 0.8), "c": (4.61, 0.8), "d": (4.19, 0.8)}
    assert _record(states, ((5, 11),)) == 0.0


def test_imbalance_is_never_negative_zero() -> None:
    states = {"a": (3.69, 0.5), "b": (4.48, 0.1), "c": (3.48, 0.3), "d": (2.7, 0.3)}
    imbalance = _record(states, ((2, 11),))
    assert imbalance == 0.0
    assert math.copysign(1.0, imbalance) == 1.0
