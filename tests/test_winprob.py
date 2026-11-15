"""Tests for win probability and its calibration."""

import random
from dataclasses import replace

import pytest

from open_engine import (
    Config,
    GameFormat,
    RatingEngine,
    RatingFormat,
    ScoringType,
    calibrate_win_probability,
    game_win_probability,
    match_win_probability,
)

FORMATS = [
    GameFormat(ScoringType.SIDE_OUT, 11, 2),
    GameFormat(ScoringType.RALLY, 21, 2, cap=25),
    GameFormat(ScoringType.SIDE_OUT, 15, 1),
]


def simulate(p: float, fmt: GameFormat, rng: random.Random) -> bool:
    a = b = 0
    while True:
        if rng.random() < p:
            a += 1
        else:
            b += 1
        for high, low, wins in ((a, b, True), (b, a, False)):
            if (high >= fmt.points_to and high - low >= fmt.win_by) or (
                fmt.cap is not None and high >= fmt.cap
            ):
                return wins


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("p", [0.45, 0.55, 0.6])
def test_game_win_probability_matches_monte_carlo(p: float, fmt: GameFormat) -> None:
    rng = random.Random(7)
    n = 200_000
    wins = sum(simulate(p, fmt, rng) for _ in range(n))
    assert abs(game_win_probability(p, fmt) - wins / n) < 0.005


@pytest.mark.parametrize("fmt", FORMATS)
def test_match_win_probability_best_of_three(fmt: GameFormat) -> None:
    p = 0.55
    g = game_win_probability(p, fmt)
    assert match_win_probability(p, fmt, 3) == pytest.approx(g**2 * (3 - 2 * g))


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("best_of", [1, 3])
def test_half_is_half(fmt: GameFormat, best_of: int) -> None:
    assert match_win_probability(0.5, fmt, best_of) == pytest.approx(0.5)


def test_calibration_identity_and_shrink() -> None:
    assert calibrate_win_probability(0.7, 1.0) == pytest.approx(0.7)
    assert 0.5 < calibrate_win_probability(0.7, 0.5) < 0.7
    low = calibrate_win_probability(0.0, 1.0)
    assert 0.0 < low < 1.0


def test_config_rejects_bad_win_calibration() -> None:
    with pytest.raises(ValueError):
        Config(win_calibration_by_scoring={ScoringType.SIDE_OUT: 1.0})
    with pytest.raises(ValueError):
        Config(win_calibration_by_scoring={ScoringType.SIDE_OUT: 0.0, ScoringType.RALLY: 1.0})


def test_fingerprint_changes_with_win_calibration() -> None:
    base = Config()
    changed = replace(
        base, win_calibration_by_scoring={ScoringType.SIDE_OUT: 0.8, ScoringType.RALLY: 1.0}
    )
    assert base.fingerprint() != changed.fingerprint()


def test_engine_win_probability_uses_calibration() -> None:
    fmt = GameFormat(ScoringType.SIDE_OUT, 11, 2)
    fmt_rating = RatingFormat.DOUBLES

    def build(a: float) -> RatingEngine:
        cfg = Config(win_calibration_by_scoring={ScoringType.SIDE_OUT: a, ScoringType.RALLY: 1.0})
        eng = RatingEngine(cfg)
        for pid, rating in (("a1", 4.5), ("a2", 4.5), ("b1", 3.5), ("b2", 3.5)):
            eng.state(pid, fmt_rating).rating = rating
        return eng

    plain = build(1.0)
    expected = match_win_probability(
        plain.predict(["a1", "a2"], ["b1", "b2"], fmt_rating, fmt.scoring), fmt, 1
    )
    got = plain.win_probability(["a1", "a2"], ["b1", "b2"], fmt_rating, fmt)
    assert got == pytest.approx(expected)
    shrunk = build(0.5).win_probability(["a1", "a2"], ["b1", "b2"], fmt_rating, fmt)
    assert 0.5 < shrunk < got
