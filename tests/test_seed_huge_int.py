"""A huge int in a seed field raises InvalidSeedError, not OverflowError."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidSeedError,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)
HUGE = 10**400


def _provisional() -> RatingState:
    return RatingEngine().seed_provisional_rating("h", D)


def _imported() -> RatingState:
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 10, 1), 60)
    return RatingEngine().seed_external_rating("h", D, snap)


ROWS: list[tuple[str, Callable[[], RatingState], dict[str, Any], str]] = [
    ("rating", _imported, {"rating": HUGE}, "rating must be between min_rating and max_rating"),
    (
        "rating_negative",
        _provisional,
        {"rating": -HUGE},
        "rating must be between min_rating and max_rating",
    ),
    (
        "rd_imported",
        _imported,
        {"rd": HUGE},
        "rd must equal external_seed_rd or the category seed RD",
    ),
    ("rd_provisional", _provisional, {"rd": HUGE}, "rd must equal new_player_rd"),
    (
        "reliability",
        _imported,
        {"initial_source_reliability": HUGE},
        "initial_source_reliability must be between 0 and 100",
    ),
]
IDS = [row[0] for row in ROWS]
PARAMS = [row[1:] for row in ROWS]


def _player(make: Callable[[], RatingState], changes: dict[str, Any]) -> Player:
    return Player("h", {D: replace(make(), **changes)})


@pytest.mark.parametrize(("make", "changes", "message"), PARAMS, ids=IDS)
def test_add_player(make: Callable[[], RatingState], changes: dict[str, Any], message: str) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=rf"Seed for h in doubles: {message}"):
        eng.add_player(_player(make, changes))
    assert eng.players == {}


@pytest.mark.parametrize(("make", "changes", "message"), PARAMS, ids=IDS)
def test_replay(make: Callable[[], RatingState], changes: dict[str, Any], message: str) -> None:
    with pytest.raises(InvalidSeedError, match=rf"Seed for h in doubles: {message}"):
        replay([], [_player(make, changes)], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("make", "changes", "message"), PARAMS, ids=IDS)
def test_prequential(
    make: Callable[[], RatingState], changes: dict[str, Any], message: str, mode: Any
) -> None:
    with pytest.raises(InvalidSeedError, match=rf"Seed for h in doubles: {message}"):
        prequential_predictions([], [_player(make, changes)], mode=mode)
