"""seed_provisional_rating does not store a category starting state (issue #112).

Under Config.category_feeds_parent, a category format (mixed, men's, or
women's doubles) starts from the player's overall doubles state. A stored
copy of that starting state would not follow later changes to doubles, so
live recording and a replay of the same matches could disagree. So
seed_provisional_rating returns the starting state there without storing
it, as state does. Elsewhere it stores as before. All ids are synthetic.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
CATEGORIES = [RatingFormat.MIXED_DOUBLES, RatingFormat.DOUBLES_MEN, RatingFormat.DOUBLES_WOMEN]
FIRST = date(2026, 1, 1)
AS_OF = date(2026, 3, 1)
OTHERS = ("syn-b", "syn-c", "syn-d")

# Labeling, not rating: a live update is counted until the next replay, and a
# category state derived live carries its parent's count. A replay gives 0.
_LABEL_FIELDS = {"live_updates_since_replay", "inherited_live_updates"}


def _snap(rating: float = 4.0, captured_on: date = FIRST) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("ext", rating, captured_on)


def _other_category(category: RatingFormat) -> RatingFormat:
    return next(fmt for fmt in CATEGORIES if fmt != category)


def _assert_live_equals_replay(live: RatingEngine, replayed: RatingEngine) -> None:
    """Every player, every format, every rating field, and the match ids agree."""
    assert sorted(live.players) == sorted(replayed.players)
    for pid in sorted(live.players):
        left = live.players[pid].ratings
        right = replayed.players[pid].ratings
        assert sorted(left) == sorted(right), pid
        for fmt in sorted(left):
            for field in fields(RatingState):
                if field.name in _LABEL_FIELDS:
                    continue
                assert getattr(left[fmt], field.name) == getattr(right[fmt], field.name), (
                    pid,
                    fmt,
                    field.name,
                )
    assert live.processed_match_ids == replayed.processed_match_ids
    assert live._reserved_match_ids == replayed._reserved_match_ids


def _seeds_without(engine: RatingEngine, pid: str, fmt: RatingFormat) -> list[Player]:
    """The engine's players as seeds, with no state for pid in fmt."""
    seeds = [copy.deepcopy(p) for p in engine.players.values()]
    for seed in seeds:
        if seed.id == pid:
            seed.ratings.pop(fmt, None)
    return seeds


def _played_doubles_engine(cfg: Config | None = None) -> RatingEngine:
    """syn-a has a doubles seed and one counted doubles match."""
    engine = RatingEngine(cfg)
    for pid in OTHERS:
        engine.seed_external_rating(pid, D, _snap())
    engine.seed_external_rating("syn-a", D, _snap())
    match = Match("syn-d0", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 3),), date(2026, 1, 5), D)
    assert engine.record(match).counted
    return engine


# -- criterion 1: a category starting state is returned, not stored ---------


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("played", [False, True], ids=["seeded parent", "played parent"])
def test_category_state_is_returned_and_not_stored(category: RatingFormat, played: bool) -> None:
    if played:
        engine = _played_doubles_engine()
    else:
        engine = RatingEngine()
        engine.seed_external_rating("syn-a", D, _snap())
    before = engine.to_snapshot()
    got = engine.seed_provisional_rating("syn-a", category)
    assert got == engine.state("syn-a", category)
    assert got.initial_source == "open_parent:doubles"
    assert category not in engine.players["syn-a"].ratings
    assert engine.to_snapshot() == before
    again = engine.seed_provisional_rating("syn-a", category)
    assert again == got
    assert again is not got


@pytest.mark.parametrize("category", CATEGORIES)
def test_provisional_category_state_is_not_stored(category: RatingFormat) -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-a", D)
    got = engine.seed_provisional_rating("syn-a", category)
    assert got == engine.state("syn-a", category)
    assert got.initial_source == "open_provisional"
    assert sorted(engine.players["syn-a"].ratings) == [D]


@pytest.mark.parametrize("category", CATEGORIES)
def test_unknown_player_gets_only_an_empty_record(category: RatingFormat) -> None:
    engine = RatingEngine()
    got = engine.seed_provisional_rating("syn-new", category)
    assert engine.players == {"syn-new": Player("syn-new")}
    cfg = engine.cfg
    assert got == RatingState(
        rating=cfg.default_rating,
        rd=cfg.new_player_rd,
        initial_rating=cfg.default_rating,
        initial_source="open_provisional",
    )


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_replayed_engine_stays_resumable(category: RatingFormat) -> None:
    seeds = [Player(pid) for pid in ("syn-a", *OTHERS)]
    ledger = [
        Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 4),), date(2026, 1, 5), D)
    ]
    engine = replay(ledger, seeds, as_of=date(2026, 1, 10))
    engine.seed_provisional_rating("syn-a", category)
    snapshot = engine.to_snapshot()
    assert snapshot["resumable"] is True
    later = Match(
        "syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), date(2026, 1, 20), category
    )
    resumed = replay([*ledger, later], as_of=AS_OF, from_snapshot=snapshot)
    full = replay([*ledger, later], seeds, as_of=AS_OF)
    assert resumed.to_snapshot() == full.to_snapshot()


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_stored_category_state_is_returned_as_before(category: RatingFormat) -> None:
    engine = RatingEngine()
    stored = engine.seed_external_rating("syn-a", category, _snap(3.25))
    assert engine.seed_provisional_rating("syn-a", category) is stored
    played = _played_doubles_engine()
    match = Match(
        "syn-c1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 1, 9), category
    )
    assert played.record(match).counted
    assert (
        played.seed_provisional_rating("syn-a", category)
        is played.players["syn-a"].ratings[category]
    )


@pytest.mark.parametrize("category", CATEGORIES)
def test_external_seed_after_provisional_needs_no_replace(category: RatingFormat) -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", D, _snap())
    engine.seed_provisional_rating("syn-a", category)
    seeded = engine.seed_external_rating("syn-a", category, _snap(3.0))
    assert seeded.initial_source == "ext"
    assert engine.state("syn-a", category).rating == 3.0


# -- criterion 2: other formats and configs store as before -----------------


@pytest.mark.parametrize("fmt", [S, D])
def test_singles_and_doubles_still_store(fmt: RatingFormat) -> None:
    engine = RatingEngine()
    got = engine.seed_provisional_rating("syn-a", fmt)
    assert engine.players["syn-a"].ratings[fmt] is got


@pytest.mark.parametrize("category", CATEGORIES)
def test_category_still_stores_without_category_feeds_parent(category: RatingFormat) -> None:
    engine = RatingEngine(Config(category_feeds_parent=False))
    engine.seed_external_rating("syn-a", D, _snap())
    got = engine.seed_provisional_rating("syn-a", category)
    assert engine.players["syn-a"].ratings[category] is got
    assert got.initial_source == "open_provisional"


# -- criterion 3: live equals replay on the issue's paths -------------------


def _base_engine(category: RatingFormat) -> RatingEngine:
    engine = RatingEngine()
    for pid in OTHERS:
        engine.seed_external_rating(pid, category, _snap())
        engine.seed_external_rating(pid, D, _snap())
    return engine


def _parent_mover(category: RatingFormat, mover: str) -> Match:
    """A counted match that moves syn-a's doubles rating."""
    fmt = D if mover == "doubles" else _other_category(category)
    return Match(
        "syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 0),), date(2026, 1, 20), fmt
    )


def _category_match(category: RatingFormat) -> Match:
    return Match(
        "syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 2, 1), category
    )


def _derived_seed(category: RatingFormat) -> Player:
    """A backend seed for syn-a with a doubles seed and its derived category state."""
    scratch = RatingEngine()
    scratch.seed_external_rating("syn-a", D, _snap())
    player = copy.deepcopy(scratch.players["syn-a"])
    player.ratings[category] = copy.deepcopy(scratch.state("syn-a", category))
    assert player.ratings[category].initial_source == "open_parent:doubles"
    return player


@pytest.mark.parametrize("mover", ["doubles", "other category"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_path_1_parent_plays_first(category: RatingFormat, mover: str) -> None:
    engine = _base_engine(category)
    engine.seed_external_rating("syn-a", D, _snap())
    engine.seed_provisional_rating("syn-a", category)
    seeds = _seeds_without(engine, "syn-a", category)
    matches = [_parent_mover(category, mover), _category_match(category)]
    for match in matches:
        assert engine.record(match).counted
    _assert_live_equals_replay(engine, replay(matches, seeds, as_of=AS_OF))


@pytest.mark.parametrize("mover", ["doubles", "other category"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_path_1_with_the_category_seed(category: RatingFormat, mover: str) -> None:
    engine = _base_engine(category)
    engine.add_player(_derived_seed(category))
    engine.seed_provisional_rating("syn-a", category)
    seeds = [copy.deepcopy(p) for p in engine.players.values()]
    assert category in next(s for s in seeds if s.id == "syn-a").ratings
    matches = [_parent_mover(category, mover), _category_match(category)]
    for match in matches:
        assert engine.record(match).counted
    _assert_live_equals_replay(engine, replay(matches, seeds, as_of=AS_OF))


def _path_2_doubles(engine: RatingEngine, reseed: bool) -> None:
    if reseed:
        engine.seed_external_rating("syn-a", D, _snap(2.5), replace_unplayed_seed=True)
    else:
        engine.seed_external_rating("syn-a", D, _snap(2.5))


@pytest.mark.parametrize("reseed", [False, True], ids=["first seed", "reseed"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_path_2_doubles_seeded_after(category: RatingFormat, reseed: bool) -> None:
    engine = _base_engine(category)
    if reseed:
        engine.seed_provisional_rating("syn-a", D)
    engine.seed_provisional_rating("syn-a", category)
    _path_2_doubles(engine, reseed)
    seeds = _seeds_without(engine, "syn-a", category)
    match = _category_match(category)
    assert engine.record(match).counted
    _assert_live_equals_replay(engine, replay([match], seeds, as_of=AS_OF))
    # The category starts from the new doubles seed.
    assert engine.players["syn-a"].ratings[D].initial_rating == 2.5


@pytest.mark.parametrize("reseed", [False, True], ids=["first seed", "reseed"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_path_2_with_the_category_seed(category: RatingFormat, reseed: bool) -> None:
    engine = _base_engine(category)
    ratings = {category: RatingState(rating=3.5, rd=engine.cfg.new_player_rd)}
    if reseed:
        ratings[D] = RatingState(rating=3.5, rd=engine.cfg.new_player_rd)
    engine.add_player(Player("syn-a", ratings))
    engine.seed_provisional_rating("syn-a", category)
    _path_2_doubles(engine, reseed)
    seeds = [copy.deepcopy(p) for p in engine.players.values()]
    assert category in next(s for s in seeds if s.id == "syn-a").ratings
    match = _category_match(category)
    assert engine.record(match).counted
    _assert_live_equals_replay(engine, replay([match], seeds, as_of=AS_OF))


# The #99 comment case. There is no "with the category state" variant: the
# category state here is derived after the parent played, so it carries
# inherited_live_updates and an rd_as_of after its source date, which the
# seed rules reject. No valid seed has that shape.
@pytest.mark.parametrize("category", CATEGORIES)
def test_partner_gap_decision_matches_replay(category: RatingFormat) -> None:
    cfg = Config(max_doubles_partner_gap=0.2)
    engine = RatingEngine(cfg)
    for pid in ("syn-b", "syn-c", "syn-d", "syn-e", "syn-f"):
        engine.seed_external_rating(pid, category, _snap())
        engine.seed_external_rating(pid, D, _snap())
    engine.seed_external_rating("syn-a", D, _snap())
    seeds = [copy.deepcopy(p) for p in engine.players.values()]
    first = Match(
        "syn-d1", ("syn-a", "syn-e"), ("syn-c", "syn-d"), ((11, 0),), date(2026, 1, 10), D
    )
    assert engine.record(first).counted
    engine.seed_provisional_rating("syn-a", category)
    matches = [first]
    for day in range(12, 16):
        match = Match(
            f"syn-d{day}", ("syn-a", "syn-f"), ("syn-c", "syn-d"), ((0, 11),), date(2026, 1, day), D
        )
        assert engine.record(match).counted
        matches.append(match)
    final = _category_match(category)
    matches.append(final)
    result = engine.record(final)
    replayed = replay(matches, seeds, cfg=cfg, as_of=AS_OF)
    assert result.counted == ("syn-m2" in replayed.processed_match_ids)
    _assert_live_equals_replay(engine, replayed)
