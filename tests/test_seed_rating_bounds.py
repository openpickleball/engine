"""A seed rating must be within the configured rating bounds."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)


def _provisional() -> RatingState:
    return RatingEngine().seed_provisional_rating("r", D)


def _imported() -> RatingState:
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 10, 1), 60)
    return RatingEngine().seed_external_rating("r", D, snap)


CFG = Config()
OUT_OF_BOUNDS = [99.0, -5.0, CFG.max_rating + 0.001, CFG.min_rating - 0.001]
MAKERS = {"provisional": _provisional, "imported": _imported}


@pytest.mark.parametrize("rating", OUT_OF_BOUNDS)
@pytest.mark.parametrize("make", MAKERS.values(), ids=list(MAKERS))
def test_add_player_rejects_out_of_bounds_rating(
    make: Callable[[], RatingState], rating: float
) -> None:
    eng = RatingEngine()
    player = Player("r", {D: replace(make(), rating=rating)})
    with pytest.raises(
        InvalidSeedError,
        match="Seed for r in doubles: rating must be between min_rating and max_rating",
    ):
        eng.add_player(player)
    assert eng.players == {}


@pytest.mark.parametrize("rating", OUT_OF_BOUNDS)
def test_replay_rejects_out_of_bounds_rating(rating: float) -> None:
    player = Player("r", {D: replace(_imported(), rating=rating)})
    with pytest.raises(
        InvalidSeedError,
        match="Seed for r in doubles: rating must be between min_rating and max_rating",
    ):
        replay([], [player], as_of=AS_OF)


@pytest.mark.parametrize("rating", [CFG.min_rating, CFG.max_rating, 5])
def test_rating_on_the_bounds_is_accepted(rating: float) -> None:
    player = Player("r", {D: replace(_imported(), rating=rating)})
    assert replay([], [player], as_of=AS_OF).players["r"].ratings[D].rating == rating
