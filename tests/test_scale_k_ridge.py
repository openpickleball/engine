"""Scaling the scale and K together changes no prediction (issue #149).

docs/model.md ("Scale and K together") states that multiplying every scale,
k_min, and k_max by the same factor leaves every expected point share the
same and multiplies each rating's distance from default_rating by that
factor, as long as no rating reaches min_rating or max_rating. These tests
check that claim on a synthetic ledger, and check that a bound breaks it.
All ids are synthetic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
)

PLAYERS = tuple(f"ridge-{i}" for i in range(10))
FORMATS = (RatingFormat.DOUBLES, RatingFormat.MIXED_DOUBLES, RatingFormat.SINGLES)
MATCH_TYPES = (MatchType.LEAGUE, MatchType.TOURNAMENT)
WIDE = {"min_rating": -1000.0, "max_rating": 1000.0}
TOLERANCE = 1e-9


@dataclass(frozen=True)
class Prediction:
    """One expected point share, taken just before its match is recorded."""

    match_id: str
    share: float


def _ledger(seed: int, count: int) -> list[Match]:
    """A synthetic ledger of one-game and three-game matches across three formats."""
    rng = random.Random(seed)
    matches = []
    day = date(2026, 1, 5)
    for index in range(count):
        rating_format = FORMATS[index % len(FORMATS)]
        size = rating_format.team_size
        ids = rng.sample(PLAYERS, 2 * size)
        best_of = rng.choice((1, 3))
        games = []
        wins = {True: 0, False: 0}
        while max(wins.values()) * 2 <= best_of:
            a_wins = rng.random() < 0.5
            loser = rng.randint(0, 9)
            games.append((11, loser) if a_wins else (loser, 11))
            wins[a_wins] += 1
        matches.append(
            Match(
                match_id=f"ridge-match-{index}",
                team_a=tuple(ids[:size]),
                team_b=tuple(ids[size:]),
                games=tuple(games),
                played_on=day,
                rating_format=rating_format,
                match_type=rng.choice(MATCH_TYPES),
                best_of=best_of,
                game_format=GameFormat(),
            )
        )
        day += timedelta(days=rng.randint(0, 6))
    return matches


def _scaled(factor: float, **bounds: float) -> Config:
    """The default config with every scale, k_min, and k_max multiplied by factor."""
    base = Config()
    return Config(
        scale_by_scoring={s: v * factor for s, v in base.scale_by_scoring.items()},
        k_min=base.k_min * factor,
        k_max=base.k_max * factor,
        **bounds,
    )


def _run(cfg: Config, matches: list[Match]) -> tuple[RatingEngine, list[Prediction], bool]:
    """Record matches in order; return the engine, each prior prediction, and any clamp."""
    engine = RatingEngine(cfg)
    predictions = []
    clamped = False
    for match in matches:
        share = engine.predict_point_share(
            match.team_a, match.team_b, match.rating_format, ScoringType.SIDE_OUT
        )
        predictions.append(Prediction(match.match_id, share))
        result = engine.record(match)
        assert result.reason is None
        clamped = clamped or bool(result.clamped)
    return engine, predictions, clamped


@pytest.mark.parametrize("factor", [0.5, 1.5, 2.0, 2.9 / 0.868, 4.0])
@pytest.mark.parametrize("seed", [1, 2])
def test_scale_and_k_together_change_no_prediction(seed: int, factor: float) -> None:
    matches = _ledger(seed, 90)
    base, base_predictions, base_clamped = _run(_scaled(1.0, **WIDE), matches)
    scaled, scaled_predictions, scaled_clamped = _run(_scaled(factor, **WIDE), matches)
    assert not base_clamped and not scaled_clamped

    for before, after in zip(base_predictions, scaled_predictions, strict=True):
        assert before.match_id == after.match_id
        assert after.share == pytest.approx(before.share, abs=TOLERANCE)

    default = Config().default_rating
    compared = 0
    for pid in PLAYERS:
        for rating_format in FORMATS:
            if rating_format not in base.players[pid].ratings:
                continue
            base_state = base.state(pid, rating_format)
            scaled_state = scaled.state(pid, rating_format)
            expected = factor * (base_state.rating - default)
            assert scaled_state.rating - default == pytest.approx(expected, abs=TOLERANCE)
            assert scaled_state.rd == pytest.approx(base_state.rd, abs=TOLERANCE)
            compared += 1
    assert compared > len(PLAYERS)


def test_a_rating_bound_breaks_the_match() -> None:
    matches = _ledger(1, 90)
    _, base_predictions, base_clamped = _run(_scaled(1.0), matches)
    _, scaled_predictions, scaled_clamped = _run(_scaled(4.0), matches)
    assert not base_clamped
    assert scaled_clamped
    gaps = [
        abs(a.share - b.share) for a, b in zip(base_predictions, scaled_predictions, strict=True)
    ]
    assert max(gaps) > 1e-3
