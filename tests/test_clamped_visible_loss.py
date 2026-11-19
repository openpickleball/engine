"""clamped lists a range cut or a loss visible at 5 places, not rounding noise (issue #129)."""

from __future__ import annotations

import itertools
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


def _engine(cfg: Config, fmt: RatingFormat, ratings: dict[str, float]) -> RatingEngine:
    engine = RatingEngine(cfg)
    for pid, rating in ratings.items():
        state = RatingState(rating=rating, rd=cfg.new_player_rd, initial_rating=rating)
        engine.add_player(Player(pid, {fmt: state}))
    return engine


def _singles(
    cfg: Config, a: float, b: float, games: tuple[tuple[int, int], ...]
) -> tuple[str, ...]:
    engine = _engine(cfg, S, {"a": a, "b": b})
    result = engine.record(Match("m1", ("a",), ("b",), games, ON, S, MatchType.TOURNAMENT))
    assert result.counted
    return result.clamped


def test_rounding_at_2_to_the_14_is_not_clamped() -> None:
    cfg = Config(min_rating=0.0, max_rating=30000.0, default_rating=1500.0)
    assert _singles(cfg, 16384.0, 16385.0, ((11, 0),)) == ()


def test_rounding_near_1e6_is_not_clamped() -> None:
    cfg = Config(min_rating=1e6, max_rating=1e6 + 6, default_rating=1e6 + 1.5)
    assert _singles(cfg, 1e6 + 2.0, 1e6 + 2.0, ((11, 7),)) == ()


def test_only_the_player_at_the_ceiling_is_clamped() -> None:
    cfg = Config(min_rating=1e6, max_rating=1e6 + 6, default_rating=1e6 + 1.5)
    assert _singles(cfg, 1e6 + 6, 1e6 + 2.0, ((11, 0),)) == ("a",)


OFFSETS = (-3.7, -0.25, 0.0, 0.5, 2.0, 7.3)
GAMES = (((11, 0),), ((0, 11),), ((11, 9),), ((7, 11),))


@pytest.mark.parametrize("center", [1500.0, 16500.0, 20000.0, -20000.0, 5e5, 1e6])
def test_no_clamp_away_from_the_bounds(center: float) -> None:
    cfg = Config(
        min_rating=center - 1000.0,
        max_rating=center + 1000.0,
        default_rating=center,
        max_doubles_partner_gap=None,
    )
    for (off_a, off_b), games in itertools.product(itertools.product(OFFSETS, repeat=2), GAMES):
        assert _singles(cfg, center + off_a, center + off_b, games) == ()
        engine = _engine(
            cfg,
            D,
            {"a": center + off_a, "b": center - off_b, "c": center + off_b, "d": center},
        )
        result = engine.record(
            Match("m1", ("a", "b"), ("c", "d"), games, ON, D, MatchType.TOURNAMENT)
        )
        assert result.counted
        assert result.clamped == ()


def test_huge_rating_still_lists_the_absorbed_player() -> None:
    cfg = Config(min_rating=0.0, max_rating=1e18, default_rating=3.5)
    engine = _engine(cfg, S, {"big": 1e17})
    result = engine.record(
        Match("m1", ("big",), ("small",), ((0, 11),), ON, S, MatchType.TOURNAMENT)
    )
    assert result.clamped == ("big",)
    assert result.imbalance == 0.6
