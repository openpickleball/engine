"""Match performance is pooled point share, so a winner can lose rating (issue #20)."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

import pytest

from open_engine import Config, Match, MatchResult, RatingEngine, RatingFormat

D = RatingFormat.DOUBLES
ON = date(2026, 10, 29)
RD = 0.42
TEAM_A = ("a1", "a2")
TEAM_B = ("b1", "b2")
WIN_WEIGHTS = [0.0, 0.25, 0.5, 1.0]

# Every valid one-game side-out score to 11, win by 2, from team A's side.
_A_LOSES = [(p, 11) for p in range(10)] + [(10, 12), (11, 13)]
ONE_GAME_SCORES = sorted(
    _A_LOSES + [(b, a) for a, b in _A_LOSES],
    key=lambda score: score[0] / (score[0] + score[1]),
)


def _record(
    rating_a: float,
    rating_b: float,
    games: tuple[tuple[int, int], ...],
    win_weight: float = 0.0,
    best_of: int = 1,
    cfg: Config | None = None,
) -> MatchResult:
    """Record one doubles match between two teams with RD 0.42 and no inactivity."""
    eng = RatingEngine(cfg if cfg is not None else Config(win_weight=win_weight))
    for team, rating in ((TEAM_A, rating_a), (TEAM_B, rating_b)):
        for pid in team:
            state = eng.seed_provisional_rating(pid, D)
            state.rating = rating
            state.rd = RD
            state.rd_as_of = ON
    result = eng.record(Match("m", TEAM_A, TEAM_B, games, ON, D, best_of=best_of))
    assert result.counted
    assert result.clamped == ()
    return result


def test_default_win_weight_is_point_share() -> None:
    assert Config().win_weight == 0.0


def test_higher_rated_winner_can_lose_rating() -> None:
    result = _record(5.0, 4.0, ((11, 9),), cfg=Config())

    assert result.actual_a == pytest.approx(0.55)
    for pid in TEAM_A:
        assert result.deltas[pid] == pytest.approx(-0.03531, abs=1e-5)
    for pid in TEAM_B:
        assert result.deltas[pid] == pytest.approx(0.03531, abs=1e-5)


def test_best_of_three_winner_with_fewer_points_loses_rating() -> None:
    result = _record(4.0, 4.0, ((11, 9), (0, 11), (11, 9)), best_of=3, cfg=Config())

    assert result.actual_a == pytest.approx(22 / 51, abs=1e-5)
    for pid in TEAM_A:
        assert result.deltas[pid] < 0.0
    for pid in TEAM_B:
        assert result.deltas[pid] > 0.0


@pytest.mark.parametrize("win_weight", WIN_WEIGHTS)
def test_delta_is_monotonic_in_margin(win_weight: float) -> None:
    results = [_record(5.0, 4.0, (score,), win_weight=win_weight) for score in ONE_GAME_SCORES]
    a_deltas = [r.deltas["a1"] for r in results]
    b_deltas = [r.deltas["b1"] for r in results]

    for lower, higher in pairwise(a_deltas):
        if win_weight < 1.0:
            assert higher > lower
        else:
            assert higher >= lower
    for lower, higher in pairwise(b_deltas):
        assert higher <= lower


@pytest.mark.parametrize("gap", [0.0, 0.5, 1.0, 2.0, 3.0])
@pytest.mark.parametrize("score", [(11, 9), (12, 10), (11, 0)])
def test_full_win_weight_winner_never_loses(gap: float, score: tuple[int, int]) -> None:
    result = _record(4.0 + gap / 2, 4.0 - gap / 2, (score,), win_weight=1.0)

    for pid in TEAM_A:
        assert result.deltas[pid] >= 0.0
    for pid in TEAM_B:
        assert result.deltas[pid] <= 0.0
