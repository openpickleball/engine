"""A rounding loss is listed in clamped from 5e-6 up, not below (issue #129)."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import Config, Match, MatchType, Player, RatingEngine, RatingFormat, RatingState

S = RatingFormat.SINGLES
ON = date(2026, 1, 1)
CFG = Config(min_rating=-(2.0**40), max_rating=2.0**40, default_rating=0.0)


def _record(base: float, gap: float, loser_points: int) -> tuple[float, tuple[str, ...]]:
    """a at base beats b at base + gap; return a's applied change and clamped."""
    engine = RatingEngine(CFG)
    for pid, rating in (("a", base), ("b", base + gap)):
        state = RatingState(rating=rating, rd=CFG.new_player_rd, initial_rating=rating)
        engine.add_player(Player(pid, {S: state}))
    match = Match("m1", ("a",), ("b",), ((11, loser_points),), ON, S, MatchType.TOURNAMENT)
    result = engine.record(match)
    assert result.counted
    return engine.state("a", S).rating - base, result.clamped


@pytest.mark.parametrize(
    ("base", "gap", "loser_points", "low", "high", "expected"),
    [
        # The gaps are multiples of 2**-6, exact at every base, so the raw
        # step is the same as at the reference rating of 3.0.
        pytest.param(2.0**35, 0.671875, 1, 3.5e-6, 5e-6, (), id="2**35-unlisted"),
        pytest.param(2.0**36, 0.78125, 9, 4.9e-6, 5e-6, (), id="2**36-just-under"),
        pytest.param(2.0**36, 0.984375, 5, 7e-6, 8e-6, ("a", "b"), id="2**36-listed"),
    ],
)
def test_rounding_loss_threshold(
    base: float,
    gap: float,
    loser_points: int,
    low: float,
    high: float,
    expected: tuple[str, ...],
) -> None:
    reference, reference_clamped = _record(3.0, gap, loser_points)
    applied, clamped = _record(base, gap, loser_points)
    assert reference_clamped == ()
    assert low <= abs(applied - reference) < high
    assert clamped == expected
