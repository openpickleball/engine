"""Seed states must be ones the engine could have produced before any match (issue #15)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
SNAP_DATE = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)


def _snapshot(reliability: float | None = 60) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE, reliability)


def _provisional(cfg: Config) -> RatingState:
    return RatingEngine(cfg).seed_provisional_rating("p", D)


def _imported(cfg: Config) -> RatingState:
    return RatingEngine(cfg).seed_external_rating("p", D, _snapshot())


def _partners(n: int) -> dict[str, date]:
    return {f"partner{i}": date(2026, 9, 1) for i in range(n)}


# Each row: a valid seed with one rule broken, and the rule's message.
REJECTED: list[tuple[str, Callable[[Config], RatingState], dict[str, object], str]] = [
    ("matches_played", _provisional, {"matches_played": 1}, "matches_played must be 0"),
    ("last_played", _provisional, {"last_played": date(2026, 9, 1)}, "last_played must be None"),
    ("partners", _provisional, {"partners": _partners(1)}, "partners must be empty"),
    ("opponents", _provisional, {"opponents": _partners(1)}, "opponents must be empty"),
    (
        "live_updates",
        _provisional,
        {"live_updates_since_replay": 1},
        "live_updates_since_replay must be 0",
    ),
    # Issue #15 repro: RD 0.10 and 20 partners with zero matches.
    ("issue_repro", _provisional, {"rd": 0.10, "partners": _partners(20)}, "partners must be"),
    ("provisional_rd", _provisional, {"rd": 0.10}, "rd must equal new_player_rd"),
    ("provisional_rd_as_of", _provisional, {"rd_as_of": SNAP_DATE}, "rd_as_of must be None"),
    (
        "provisional_reliability",
        _provisional,
        {"initial_source_reliability": 90.0},
        "initial_source_reliability must be None",
    ),
    ("imported_rd", _imported, {"rd": 0.10}, "rd must equal external_seed_rd"),
    ("imported_rd_max", _imported, {"rd": 0.80}, "rd must equal external_seed_rd"),
    ("imported_rd_as_of_none", _imported, {"rd_as_of": None}, "rd_as_of must equal"),
    ("imported_rd_as_of_late", _imported, {"rd_as_of": AS_OF}, "rd_as_of must equal"),
    (
        "imported_reliability_high",
        _imported,
        {"initial_source_reliability": 101.0},
        "initial_source_reliability must be between 0 and 100",
    ),
    (
        "imported_reliability_low",
        _imported,
        {"initial_source_reliability": -1.0},
        "initial_source_reliability must be between 0 and 100",
    ),
]


def _bad_player(make: Callable[[Config], RatingState], changes: dict[str, object]) -> Player:
    state = replace(make(Config()), **changes)  # type: ignore[arg-type]
    return Player(id="p", ratings={D: state})


@pytest.mark.parametrize(
    ("make", "changes", "message"),
    [row[1:] for row in REJECTED],
    ids=[row[0] for row in REJECTED],
)
def test_add_player_rejects_seed(
    make: Callable[[Config], RatingState], changes: dict[str, object], message: str
) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=rf"Seed for p in doubles: {message}"):
        eng.add_player(_bad_player(make, changes))
    assert eng.players == {}


@pytest.mark.parametrize(
    ("make", "changes", "message"),
    [row[1:] for row in REJECTED],
    ids=[row[0] for row in REJECTED],
)
def test_replay_rejects_seed(
    make: Callable[[Config], RatingState], changes: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidSeedError, match=rf"Seed for p in doubles: {message}"):
        replay([], [_bad_player(make, changes)], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_rejects_seed(mode: str) -> None:
    bad = _bad_player(_provisional, {"rd": 0.10, "partners": _partners(20)})
    ledger = [Match("m1", ("p", "q"), ("r", "s"), ((11, 7),), SNAP_DATE, D)]
    with pytest.raises(InvalidSeedError, match="Seed for p in doubles"):
        prequential_predictions(ledger, [bad], mode=mode)  # type: ignore[arg-type]


def test_first_error_is_in_sorted_format_order() -> None:
    bad = replace(_provisional(Config()), rd=0.10)
    player = Player(id="p", ratings={D: bad, RatingFormat.SINGLES: bad, MIXED: bad})
    with pytest.raises(InvalidSeedError, match="Seed for p in doubles:"):
        RatingEngine().add_player(player)


def _engine_seeds(cfg: Config) -> list[Player]:
    """
    Every kind of unplayed state the engine itself produces or starts from.

    Under category_feeds_parent the engine stores no category starting
    state (issue #112), so each one is read with state and put in the seed,
    as a backend can.
    """
    eng = RatingEngine(cfg)
    eng.seed_external_rating("imp", D, _snapshot())
    eng.seed_external_rating("noreli", D, _snapshot(reliability=None))
    eng.seed_provisional_rating("prov", D)
    eng.seed_provisional_rating("prov", RatingFormat.SINGLES)
    eng.seed_external_rating("single", RatingFormat.SINGLES, _snapshot(reliability=100))
    eng.seed_provisional_rating("other", D)
    seeds = [copy.deepcopy(eng.players[pid]) for pid in sorted(eng.players)]
    # imp: category derived from an imported parent; prov: category with a
    # provisional parent.
    for seed in seeds:
        if seed.id in ("imp", "noreli", "prov"):
            seed.ratings[MIXED] = copy.deepcopy(eng.state(seed.id, MIXED))
    return seeds


@pytest.mark.parametrize(
    "cfg",
    [Config(), Config(external_seed_rd=0.6, category_seed_rd=0.5)],
    ids=["default", "external_rd_above_category"],
)
def test_engine_produced_seeds_replay_like_live(cfg: Config) -> None:
    seeds = _engine_seeds(cfg)
    ledger = [
        Match("m1", ("imp", "prov"), ("noreli", "other"), ((11, 7),), date(2026, 10, 20), MIXED)
    ]

    replayed = replay(ledger, seeds, cfg=cfg, as_of=AS_OF)

    live = RatingEngine(cfg)
    for player in seeds:
        live.add_player(player)
    live.record(ledger[0], as_of=AS_OF)
    for pid, player in live.players.items():
        for fmt, state in player.ratings.items():
            assert replayed.players[pid].ratings[fmt].rating == state.rating
            assert replayed.players[pid].ratings[fmt].rd == state.rd


def test_mixed_seed_from_imported_parent_keeps_category_rd() -> None:
    seeds = _engine_seeds(Config())
    imp = next(p for p in seeds if p.id == "imp")
    assert imp.ratings[MIXED].rd == Config().category_seed_rd
    engine = replay([], [imp], as_of=AS_OF)
    assert engine.players["imp"].ratings[MIXED].rd == Config().category_seed_rd
