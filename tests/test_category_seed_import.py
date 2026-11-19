"""An import for a category after seed_provisional_rating there (issues #112 and #79).

Since issue #112, seed_provisional_rating stores no category state under
category_feeds_parent, so a later seed_external_rating in that category
finds no stored state to replace and is accepted, with or without
replace_unplayed_seed, as for a player who never called it. That follows
the issue #79 decision: seeding a category format with no stored state
stays allowed. A stored derived state, here from an add_player seed, is
still refused. All ids are synthetic.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date

import pytest

from helpers import derived_seed
from open_engine import (
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    RatingEngine,
    RatingFormat,
)

D = RatingFormat.DOUBLES
CATEGORIES = [RatingFormat.MIXED_DOUBLES, RatingFormat.DOUBLES_MEN, RatingFormat.DOUBLES_WOMEN]
CAPTURED = date(2026, 1, 1)
IMPORT = ExternalRatingSnapshot("ext", 3.25, date(2026, 2, 1))


def _snap(rating: float = 4.0) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("ext", rating, CAPTURED)


def _no_doubles() -> RatingEngine:
    return RatingEngine()


def _dated_doubles() -> RatingEngine:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", D, _snap())
    return engine


def _played_doubles() -> RatingEngine:
    """The issue #79 repro: three counted doubles matches."""
    engine = RatingEngine()
    for i in range(3):
        match = Match(
            f"syn-m{i}", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), date(2026, 1, 5 + i), D
        )
        assert engine.record(match).counted
    return engine


SETUPS = {
    "no doubles state": _no_doubles,
    "dated doubles seed": _dated_doubles,
    "played doubles state": _played_doubles,
}


@pytest.mark.parametrize("replace", [False, True], ids=["no flag", "flag"])
@pytest.mark.parametrize("setup", list(SETUPS.values()), ids=list(SETUPS))
@pytest.mark.parametrize("category", CATEGORIES)
def test_import_after_seed_provisional_rating_is_accepted(
    category: RatingFormat, setup: Callable[[], RatingEngine], replace: bool
) -> None:
    engine = setup()
    engine.seed_provisional_rating("syn-a", category)
    doubles_before = copy.deepcopy(engine.players.get("syn-a"))
    seeded = engine.seed_external_rating("syn-a", category, IMPORT, replace_unplayed_seed=replace)
    stored = engine.players["syn-a"].ratings[category]
    assert stored is seeded
    assert stored.rating == 3.25
    assert stored.initial_source == "ext"
    assert stored.initial_source_date == IMPORT.captured_on
    assert stored.matches_played == 0
    if doubles_before is not None and D in doubles_before.ratings:
        assert engine.players["syn-a"].ratings[D] == doubles_before.ratings[D]


@pytest.mark.parametrize("replace", [False, True], ids=["no flag", "flag"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_a_stored_derived_state_from_a_seed_is_still_refused(
    category: RatingFormat, replace: bool
) -> None:
    engine = RatingEngine()
    engine.add_player(derived_seed("syn-a", _snap(), category))
    stored = engine.players["syn-a"].ratings[category]
    assert engine.seed_provisional_rating("syn-a", category) is stored
    before = engine.to_snapshot()
    with pytest.raises(
        InvalidSeedError, match=r"^Cannot replace a rating derived from OPEN overall doubles$"
    ):
        engine.seed_external_rating("syn-a", category, IMPORT, replace_unplayed_seed=replace)
    assert engine.to_snapshot() == before
