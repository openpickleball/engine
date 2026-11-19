"""A doubles reseed refreshes the unplayed category states derived from it (issue #101).

Under category_feeds_parent, a category state with initial_source
"open_parent:doubles" starts from the player's overall doubles state. When
that doubles seed is replaced with replace_unplayed_seed, a stored
unplayed derived state must follow it, or live recording and a replay of
the same matches disagree. A refused reseed changes nothing, and states not
derived from doubles are left alone. Since issue #112,
seed_provisional_rating does not store a category state under
category_feeds_parent, so these tests store one the way a backend can: a
seed passed to add_player. All ids are synthetic.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from datetime import date

import pytest

from helpers import derived_seed
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
CATEGORIES = [RatingFormat.MIXED_DOUBLES, RatingFormat.DOUBLES_MEN, RatingFormat.DOUBLES_WOMEN]
CFG = Config()
FIRST = date(2026, 1, 1)
SECOND = date(2026, 1, 15)
PLAYED = date(2026, 2, 1)
AS_OF = date(2026, 3, 1)


def _snap(
    rating: float, captured_on: date = FIRST, ext_id: str | None = None
) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("syn-ext", rating, captured_on, external_player_id=ext_id)


def _derived_engine(category: RatingFormat) -> RatingEngine:
    """syn-a has a doubles seed and a stored, unplayed state derived from it."""
    engine = RatingEngine()
    for pid in ("syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, category, _snap(4.0))
    engine.add_player(derived_seed("syn-a", _snap(4.0), category))
    derived = engine.players["syn-a"].ratings[category]
    assert derived.initial_source == "open_parent:doubles"
    return engine


def _reseed(engine: RatingEngine) -> None:
    engine.seed_external_rating("syn-a", D, _snap(2.5, SECOND), replace_unplayed_seed=True)


def _expected(rating: float, captured_on: date) -> RatingState:
    return RatingState(
        rating=rating,
        rd=max(CFG.external_seed_rd, CFG.category_seed_rd),
        rd_as_of=captured_on,
        initial_rating=rating,
        initial_source="open_parent:doubles",
        initial_source_date=captured_on,
        inherited_live_updates=0,
    )


def _assert_same_states(
    left: RatingEngine, right: RatingEngine, formats: list[RatingFormat]
) -> None:
    assert sorted(left.players) == sorted(right.players)
    for pid in sorted(left.players):
        for fmt in formats:
            lstate = left.players[pid].ratings.get(fmt)
            rstate = right.players[pid].ratings.get(fmt)
            assert (lstate is None) == (rstate is None), (pid, fmt)
            if lstate is None or rstate is None:
                continue
            for field in fields(RatingState):
                # A live update is provisional and counted until the next
                # replay; a replay counts none. That is labeling, not rating.
                if field.name == "live_updates_since_replay":
                    continue
                assert getattr(lstate, field.name) == getattr(rstate, field.name), (
                    pid,
                    fmt,
                    field.name,
                )


@pytest.mark.parametrize("category", CATEGORIES)
def test_reseed_refreshes_the_stored_derived_state(category: RatingFormat) -> None:
    engine = _derived_engine(category)
    _reseed(engine)
    assert engine.state("syn-a", category) == _expected(2.5, SECOND)


@pytest.mark.parametrize("category", CATEGORIES)
def test_live_equals_replay_with_and_without_the_derived_seed(category: RatingFormat) -> None:
    engine = _derived_engine(category)
    _reseed(engine)
    with_derived = [copy.deepcopy(p) for p in engine.players.values()]
    without: list[Player] = []
    for player in engine.players.values():
        seed = copy.deepcopy(player)
        if seed.id == "syn-a":
            del seed.ratings[category]
        without.append(seed)
    match = Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), PLAYED, category)
    result = engine.record(match)
    assert result.counted
    replayed_with = replay([match], with_derived, as_of=AS_OF)
    replayed_without = replay([match], without, as_of=AS_OF)
    _assert_same_states(engine, replayed_with, [category, D])
    _assert_same_states(engine, replayed_without, [category, D])


@pytest.mark.parametrize("category", CATEGORIES)
def test_refresh_keeps_the_reserved_shape_and_round_trips(category: RatingFormat) -> None:
    engine = _derived_engine(category)
    _reseed(engine)
    parent = engine.state("syn-a", D)
    derived = engine.state("syn-a", category)
    assert derived.initial_source_date == parent.initial_source_date == SECOND
    assert derived.matches_played == 0
    assert derived.rd >= CFG.category_seed_rd
    snapshot = engine.to_snapshot()
    restored = RatingEngine.from_snapshot(snapshot)
    assert restored.to_snapshot() == snapshot
    fresh = RatingEngine()
    fresh.add_player(copy.deepcopy(engine.players["syn-a"]))
    assert fresh.state("syn-a", category) == derived


def test_refused_reseed_without_replace_changes_nothing() -> None:
    engine = _derived_engine(MX)
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match="explicitly allow replacing an unplayed seed"):
        engine.seed_external_rating("syn-a", D, _snap(2.5, SECOND))
    assert engine.to_snapshot() == before
    assert engine.state("syn-a", MX).rating == 4.0


def test_refused_reseed_after_existing_state_checks_changes_nothing() -> None:
    engine = _derived_engine(MX)
    engine.attach_external_id("syn-z", "syn-ext", "syn-x1")
    before = engine.to_snapshot()
    with pytest.raises(PlayerError, match="already linked to another player"):
        engine.seed_external_rating(
            "syn-a", D, _snap(2.5, SECOND, ext_id="syn-x1"), replace_unplayed_seed=True
        )
    assert engine.to_snapshot() == before
    assert engine.state("syn-a", MX).rating == 4.0


def test_imported_category_state_is_not_touched() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", MX, _snap(3.0))
    engine.seed_external_rating("syn-a", D, _snap(4.0))
    imported = copy.deepcopy(engine.state("syn-a", MX))
    _reseed(engine)
    assert engine.state("syn-a", MX) == imported


def test_provisional_category_state_is_not_touched() -> None:
    engine = RatingEngine()
    engine.add_player(Player("syn-a", {MX: RatingState(rating=3.5, rd=CFG.new_player_rd)}))
    engine.seed_external_rating("syn-a", D, _snap(4.0))
    assert MX in engine.players["syn-a"].ratings
    provisional = copy.deepcopy(engine.state("syn-a", MX))
    assert provisional.initial_source == "open_provisional"
    _reseed(engine)
    assert engine.state("syn-a", MX) == provisional


def test_first_doubles_seed_creates_no_category_state() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", D, _snap(4.0))
    assert set(engine.players["syn-a"].ratings) == {D}


@pytest.mark.parametrize("reseeded", [S, MX])
def test_reseed_of_a_format_without_category_children_changes_no_other_format(
    reseeded: RatingFormat,
) -> None:
    engine = RatingEngine()
    engine.add_player(derived_seed("syn-a", _snap(4.0), RatingFormat.DOUBLES_MEN))
    engine.seed_external_rating("syn-a", S, _snap(4.0))
    assert RatingFormat.DOUBLES_MEN in engine.players["syn-a"].ratings
    if reseeded is MX:
        engine.seed_external_rating("syn-a", MX, _snap(3.0))
    others = {
        fmt: copy.deepcopy(state)
        for fmt, state in engine.players["syn-a"].ratings.items()
        if fmt is not reseeded
    }
    engine.seed_external_rating("syn-a", reseeded, _snap(2.5, SECOND), replace_unplayed_seed=True)
    assert engine.state("syn-a", reseeded).rating == 2.5
    assert {
        fmt: state for fmt, state in engine.players["syn-a"].ratings.items() if fmt is not reseeded
    } == others


def test_reseed_after_replay_clears_the_resumable_mark() -> None:
    engine = replay([], [derived_seed("syn-a", _snap(4.0), MX)], as_of=AS_OF)
    assert MX in engine.players["syn-a"].ratings
    assert engine.to_snapshot()["resumable"] is True
    _reseed(engine)
    assert engine.state("syn-a", MX) == _expected(2.5, SECOND)
    assert engine.to_snapshot()["resumable"] is False
