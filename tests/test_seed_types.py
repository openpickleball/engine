"""Seed fields of the wrong type raise InvalidSeedError, never TypeError."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidSeedError,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
SNAP_DATE = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)


def _provisional() -> RatingState:
    return RatingEngine().state("k", D)


def _imported() -> RatingState:
    snap = ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE, 60)
    return RatingEngine().seed_external_rating("k", D, snap)


def _category() -> RatingState:
    eng = RatingEngine()
    eng.seed_external_rating("k", D, ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE))
    return eng.state("k", MIXED)


ROWS: list[tuple[str, Callable[[], RatingState], dict[str, Any], str]] = [
    ("rd_decimal", _category, {"rd": Decimal("0.5")}, "rd must be a finite number"),
    ("rd_bool", _provisional, {"rd": True}, "rd must be a finite number"),
    ("rd_nan", _imported, {"rd": float("nan")}, "rd must be a finite number"),
    ("rd_str", _imported, {"rd": "0.42"}, "rd must be a finite number"),
    ("rating_str", _provisional, {"rating": "3.5"}, "rating must be a finite number"),
    ("rating_nan", _provisional, {"rating": float("nan")}, "rating must be a finite number"),
    ("rating_inf", _imported, {"rating": float("inf")}, "rating must be a finite number"),
    ("rating_neg_inf", _imported, {"rating": float("-inf")}, "rating must be a finite number"),
    ("rating_decimal", _imported, {"rating": Decimal("4")}, "rating must be a finite number"),
    (
        "reliability_str",
        _imported,
        {"initial_source_reliability": "50"},
        "initial_source_reliability must be a finite number",
    ),
    (
        "reliability_nan",
        _imported,
        {"initial_source_reliability": float("nan")},
        "initial_source_reliability must be a finite number",
    ),
    (
        "reliability_bool",
        _imported,
        {"initial_source_reliability": True},
        "initial_source_reliability must be a finite number",
    ),
    ("partners_none", _provisional, {"partners": None}, "partners must be a dict"),
    ("opponents_none", _provisional, {"opponents": None}, "opponents must be a dict"),
    ("partners_list", _imported, {"partners": []}, "partners must be a dict"),
    ("matches_bool", _provisional, {"matches_played": False}, "matches_played must be an int"),
    ("matches_float", _provisional, {"matches_played": 0.0}, "matches_played must be an int"),
    (
        "live_updates_float",
        _provisional,
        {"live_updates_since_replay": 0.0},
        "live_updates_since_replay must be an int",
    ),
    (
        "live_updates_bool",
        _imported,
        {"live_updates_since_replay": False},
        "live_updates_since_replay must be an int",
    ),
    (
        "source_date_str",
        _imported,
        {"initial_source_date": "2026-10-01", "rd_as_of": "2026-10-01"},
        "initial_source_date must be a date",
    ),
    (
        "source_date_datetime",
        _imported,
        {
            "initial_source_date": datetime(2026, 10, 1),
            "rd_as_of": datetime(2026, 10, 1),
        },
        "initial_source_date must be a date",
    ),
    ("rd_as_of_str", _imported, {"rd_as_of": "2026-10-01"}, "rd_as_of must be a date"),
]


def _player(make: Callable[[], RatingState], changes: dict[str, Any]) -> Player:
    return Player(id="k", ratings={D: replace(make(), **changes)})


@pytest.mark.parametrize(
    ("make", "changes", "message"), [r[1:] for r in ROWS], ids=[r[0] for r in ROWS]
)
def test_add_player_rejects_wrong_type(
    make: Callable[[], RatingState], changes: dict[str, Any], message: str
) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=rf"Seed for k in doubles: {message}"):
        eng.add_player(_player(make, changes))
    assert eng.players == {}


@pytest.mark.parametrize(
    ("make", "changes", "message"), [r[1:] for r in ROWS], ids=[r[0] for r in ROWS]
)
def test_replay_rejects_wrong_type(
    make: Callable[[], RatingState], changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(InvalidSeedError, match=rf"Seed for k in doubles: {message}"):
        replay([], [_player(make, changes)], as_of=AS_OF)


@pytest.mark.parametrize("key", [1, "singles", None])
def test_ratings_key_must_be_a_rating_format(key: Any) -> None:
    state = _provisional()
    ratings: dict[Any, RatingState] = {D: state, key: state}
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=r"Seed for k: ratings key .* is not a RatingFormat"):
        eng.add_player(Player("k", ratings))
    assert eng.players == {}
    with pytest.raises(InvalidSeedError, match=r"Seed for k: ratings key .* is not a RatingFormat"):
        replay([], [Player("k", ratings)], as_of=AS_OF)
