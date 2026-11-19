"""A rating change lost to float precision is reported in clamped (issue #129)."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import (
    Config,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
ON = date(2026, 1, 1)
WIDE = Config(min_rating=0.0, max_rating=1e18, default_rating=3.5, max_doubles_partner_gap=None)


def _add(engine: RatingEngine, pid: str, fmt: RatingFormat, rating: float) -> None:
    rd = engine.cfg.new_player_rd
    engine.add_player(Player(pid, {fmt: RatingState(rating=rating, rd=rd, initial_rating=rating)}))


def test_change_absorbed_by_a_huge_rating_is_clamped() -> None:
    # The issue's reproduction: big's change rounds away against 1e17.
    engine = RatingEngine(WIDE)
    _add(engine, "big", S, 1e17)
    result = engine.record(
        Match("m1", ("big",), ("small",), ((0, 11),), ON, S, MatchType.TOURNAMENT)
    )
    assert result.counted
    assert result.clamped == ("big",)
    assert result.deltas == {"big": 0.0, "small": 0.6}
    assert result.imbalance == 0.6
    assert engine.state("big", S).rating == 1e17


def test_doubles_lists_only_the_absorbed_player() -> None:
    engine = RatingEngine(WIDE)
    _add(engine, "big", D, 1e17)
    _add(engine, "mate", D, 3.5)
    result = engine.record(
        Match("m1", ("big", "mate"), ("c", "d"), ((0, 11),), ON, D, MatchType.TOURNAMENT)
    )
    assert result.counted
    assert result.clamped == ("big",)
    assert result.deltas["big"] == 0.0
    assert result.deltas["mate"] < 0.0
    assert result.imbalance != 0.0


@pytest.mark.parametrize(
    ("rating", "games", "expected"),
    [
        pytest.param(2.0, ((0, 11),), ("a",), id="loser-at-floor"),
        pytest.param(8.0, ((11, 0),), ("a",), id="winner-at-ceiling"),
        pytest.param(4.0, ((11, 7),), (), id="ordinary-match"),
    ],
)
def test_default_config_clamps_unchanged(
    rating: float, games: tuple[tuple[int, int], ...], expected: tuple[str, ...]
) -> None:
    engine = RatingEngine(Config(max_singles_rating_gap=None))
    _add(engine, "a", S, rating)
    _add(engine, "b", S, 4.0)
    result = engine.record(Match("m1", ("a",), ("b",), games, ON, S, MatchType.TOURNAMENT))
    assert result.counted
    assert result.clamped == expected
