"""Seed and identity entry points reject blank and shared identities (issue #52).

Also covers #44 (seeds bypass account and external identity uniqueness)
and #45 (seeds accept blank player, account, and external ids). A seed
must not carry a blank id, an initial_rating outside the rating range, or
an account or external identity another player already holds. All ids
are synthetic.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
CFG = Config()
AS_OF = date(2026, 11, 1)


def _provisional(**changes: Any) -> Any:
    state = RatingEngine(CFG).seed_provisional_rating("syn-x", D)
    return replace(state, **changes)


# -- blank values and initial_rating: InvalidSeedError -----------------------

BLANK_ROWS: list[tuple[str, Player, str]] = [
    ("empty_player_id", Player("", {}), r"^Seed player id must not be blank$"),
    ("space_player_id", Player("  ", {}), r"^Seed player id must not be blank$"),
    (
        "empty_account",
        Player("syn-a", {}, account_user_id=""),
        r"^Seed for syn-a: account_user_id must not be blank$",
    ),
    (
        "space_account",
        Player("syn-a", {}, account_user_id="   "),
        r"^Seed for syn-a: account_user_id must not be blank$",
    ),
    (
        "empty_source_and_id",
        Player("syn-a", {}, external_ids={"": ""}),
        r"^Seed for syn-a: external_ids must not have a blank source or id$",
    ),
    (
        "space_source",
        Player("syn-a", {}, external_ids={" ": "x1"}),
        r"^Seed for syn-a: external_ids must not have a blank source or id$",
    ),
    (
        "space_external_id",
        Player("syn-a", {}, external_ids={"src": "\t"}),
        r"^Seed for syn-a: external_ids must not have a blank source or id$",
    ),
    (
        "initial_rating_high",
        Player("syn-a", {D: _provisional(initial_rating=99.0)}),
        r"^Seed for syn-a in doubles: initial_rating must be between min_rating and max_rating$",
    ),
    (
        "initial_rating_low",
        Player("syn-a", {D: _provisional(initial_rating=CFG.min_rating - 0.01)}),
        r"^Seed for syn-a in doubles: initial_rating must be between min_rating and max_rating$",
    ),
    (
        "initial_rating_nan",
        Player("syn-a", {D: _provisional(initial_rating=float("nan"))}),
        r"^Seed for syn-a in doubles: initial_rating must be a finite number$",
    ),
    (
        "initial_rating_str",
        Player("syn-a", {D: _provisional(initial_rating="3.5")}),
        r"^Seed for syn-a in doubles: initial_rating must be a finite number$",
    ),
    (
        "initial_rating_bool",
        Player("syn-a", {D: _provisional(initial_rating=True)}),
        r"^Seed for syn-a in doubles: initial_rating must be a finite number$",
    ),
]
BLANK_IDS = [row[0] for row in BLANK_ROWS]
BLANK_PARAMS = [row[1:] for row in BLANK_ROWS]


@pytest.mark.parametrize(("seed", "message"), BLANK_PARAMS, ids=BLANK_IDS)
def test_add_player_rejects_bad_value(seed: Player, message: str) -> None:
    eng = RatingEngine(CFG)
    eng.link_account("syn-keep", "acct-keep")
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match=message):
        eng.add_player(seed)
    assert eng.to_snapshot() == before


@pytest.mark.parametrize(("seed", "message"), BLANK_PARAMS, ids=BLANK_IDS)
def test_replay_rejects_bad_value(seed: Player, message: str) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        replay([], [seed], cfg=CFG, as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("seed", "message"), BLANK_PARAMS, ids=BLANK_IDS)
def test_prequential_rejects_bad_value(seed: Player, message: str, mode: Any) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        prequential_predictions([], [seed], cfg=CFG, mode=mode)


def test_initial_rating_at_the_bounds_is_accepted() -> None:
    seeds = [
        Player("syn-lo", {D: _provisional(initial_rating=CFG.min_rating)}),
        Player("syn-hi", {D: _provisional(initial_rating=CFG.max_rating)}),
    ]
    engine = replay([], seeds, cfg=CFG, as_of=AS_OF)
    assert RatingEngine.from_snapshot(engine.to_snapshot(), cfg=CFG).players == engine.players


# -- shared identities: PlayerError ------------------------------------------

ACCOUNT_TAKEN = r"^This account is already linked to another player profile$"
EXTERNAL_TAKEN = r"^This external identity is already linked to another player$"

SHARED_ROWS: list[tuple[str, Player, Player, str]] = [
    (
        "account",
        Player("syn-a", {}, account_user_id="acct-1"),
        Player("syn-b", {}, account_user_id="acct-1"),
        ACCOUNT_TAKEN,
    ),
    (
        "external",
        Player("syn-a", {}, external_ids={"src": "x1"}),
        Player("syn-b", {}, external_ids={"other": "y", "src": "x1"}),
        EXTERNAL_TAKEN,
    ),
]
SHARED_IDS = [row[0] for row in SHARED_ROWS]
SHARED_PARAMS = [row[1:] for row in SHARED_ROWS]


@pytest.mark.parametrize(("first", "second", "message"), SHARED_PARAMS, ids=SHARED_IDS)
def test_add_player_rejects_identity_held_by_existing_player(
    first: Player, second: Player, message: str
) -> None:
    eng = RatingEngine(CFG)
    eng.add_player(first)
    before = eng.to_snapshot()
    with pytest.raises(PlayerError, match=message):
        eng.add_player(second)
    assert eng.to_snapshot() == before


@pytest.mark.parametrize(("first", "second", "message"), SHARED_PARAMS, ids=SHARED_IDS)
def test_replay_rejects_shared_identity_in_batch(
    first: Player, second: Player, message: str
) -> None:
    with pytest.raises(PlayerError, match=message):
        replay([], [first, second], cfg=CFG, as_of=AS_OF)


@pytest.mark.parametrize(("first", "second", "message"), SHARED_PARAMS, ids=SHARED_IDS)
def test_resume_rejects_identity_held_by_checkpoint_player(
    first: Player, second: Player, message: str
) -> None:
    checkpoint = replay([], [first], cfg=CFG, as_of=AS_OF).to_snapshot()
    with pytest.raises(PlayerError, match=message):
        replay([], [second], cfg=CFG, as_of=AS_OF, from_snapshot=checkpoint)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("first", "second", "message"), SHARED_PARAMS, ids=SHARED_IDS)
def test_prequential_rejects_shared_identity_in_batch(
    first: Player, second: Player, message: str, mode: Any
) -> None:
    with pytest.raises(PlayerError, match=message):
        prequential_predictions([], [first, second], cfg=CFG, mode=mode)


def test_add_player_rejects_identity_from_seed_external_rating() -> None:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), external_player_id="X")
    eng.seed_external_rating("syn-a", D, snap)
    before = eng.to_snapshot()
    with pytest.raises(PlayerError, match=EXTERNAL_TAKEN):
        eng.add_player(Player("syn-b", {}, external_ids={"ext": "X"}))
    assert eng.to_snapshot() == before


def test_add_player_rejects_account_from_link_account() -> None:
    eng = RatingEngine(CFG)
    eng.link_account("syn-a", "acct-1")
    with pytest.raises(PlayerError, match=ACCOUNT_TAKEN):
        eng.add_player(Player("syn-b", {}, account_user_id="acct-1"))
    assert set(eng.players) == {"syn-a"}


def test_same_external_id_from_different_sources_is_accepted() -> None:
    seeds = [
        Player("syn-a", {}, external_ids={"src-1": "x1"}, account_user_id="acct-1"),
        Player("syn-b", {}, external_ids={"src-2": "x1"}, account_user_id="acct-2"),
    ]
    engine = replay([], seeds, cfg=CFG, as_of=AS_OF)
    assert set(engine.players) == {"syn-a", "syn-b"}


# -- link_account, attach_external_id, seed_external_rating ------------------

LINK_ROWS: list[tuple[str, Callable[[RatingEngine], object], str]] = [
    (
        "link_space_account",
        lambda e: e.link_account("syn-c", "   "),
        r"^account_user_id is required$",
    ),
    (
        "attach_space_source",
        lambda e: e.attach_external_id("syn-c", " ", "id"),
        r"^source and external_player_id are required$",
    ),
    (
        "attach_space_external_id",
        lambda e: e.attach_external_id("syn-c", "src", "\n"),
        r"^source and external_player_id are required$",
    ),
]


@pytest.mark.parametrize(
    ("call", "message"), [row[1:] for row in LINK_ROWS], ids=[row[0] for row in LINK_ROWS]
)
def test_identity_methods_reject_whitespace(
    call: Callable[[RatingEngine], object], message: str
) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(PlayerError, match=message):
        call(eng)
    assert eng.players == {}


# Issue #74: a blank source or external id in seed_external_rating is a bad
# seed field, checked with the other snapshot fields.
SEED_EXTERNAL_BLANK_ROWS: list[tuple[str, ExternalRatingSnapshot, str]] = [
    (
        "seed_external_space_external_id",
        ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), external_player_id=" "),
        r"^External snapshot external_player_id must not be blank$",
    ),
    (
        "seed_external_space_source",
        ExternalRatingSnapshot(" ", 4.0, date(2026, 10, 1), external_player_id="X"),
        r"^External snapshot source must not be blank$",
    ),
]


@pytest.mark.parametrize(
    ("snap", "message"),
    [row[1:] for row in SEED_EXTERNAL_BLANK_ROWS],
    ids=[row[0] for row in SEED_EXTERNAL_BLANK_ROWS],
)
def test_seed_external_rating_rejects_whitespace(
    snap: ExternalRatingSnapshot, message: str
) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(InvalidSeedError, match=message):
        eng.seed_external_rating("syn-c", D, snap)
    assert eng.players == {}


NON_STR_ROWS: list[tuple[str, Callable[[RatingEngine], object], str]] = [
    ("link_int_account", lambda e: e.link_account("syn-c", 5), r"^account_user_id is required$"),
    (
        "link_bytes_account",
        lambda e: e.link_account("syn-c", b"acct"),
        r"^account_user_id is required$",
    ),
    (
        "attach_int_external_id",
        lambda e: e.attach_external_id("syn-c", "src", 7),
        r"^source and external_player_id are required$",
    ),
    ("link_int_player_id", lambda e: e.link_account(5, "acct-1"), r"^player_id is required$"),
    (
        "attach_int_player_id",
        lambda e: e.attach_external_id(5, "src", "x1"),
        r"^player_id is required$",
    ),
    ("player_int_id", lambda e: e.player(5), r"^player_id is required$"),
    ("state_int_id", lambda e: e.state(5, D), r"^player_id is required$"),
    (
        "summary_int_id",
        lambda e: e.player_summary(5, D, as_of=AS_OF),
        r"^player_id is required$",
    ),
    (
        "seed_provisional_int_id",
        lambda e: e.seed_provisional_rating(5, D),
        r"^player_id is required$",
    ),
    (
        "seed_external_int_id",
        lambda e: e.seed_external_rating(5, D, ExternalRatingSnapshot("ext", 4.0, AS_OF)),
        r"^player_id is required$",
    ),
]


@pytest.mark.parametrize(
    ("call", "message"), [row[1:] for row in NON_STR_ROWS], ids=[row[0] for row in NON_STR_ROWS]
)
def test_identity_methods_reject_non_str(
    call: Callable[[RatingEngine], object], message: str
) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(PlayerError, match=message):
        call(eng)
    assert eng.players == {}


def test_resume_conflict_leaves_the_checkpoint_unchanged() -> None:
    first = Player("syn-a", {}, account_user_id="acct-1")
    checkpoint = replay([], [first], cfg=CFG, as_of=AS_OF).to_snapshot()
    before = copy.deepcopy(checkpoint)
    seeds = [
        Player("syn-b", {}, account_user_id="acct-2"),
        Player("syn-c", {}, account_user_id="acct-1"),
    ]
    with pytest.raises(PlayerError, match=ACCOUNT_TAKEN):
        replay([], seeds, cfg=CFG, as_of=AS_OF, from_snapshot=checkpoint)
    assert checkpoint == before
    resumed = replay([], seeds[:1], cfg=CFG, as_of=AS_OF, from_snapshot=checkpoint)
    assert set(resumed.players) == {"syn-a", "syn-b"}


def test_many_seeds_are_checked_in_linear_time() -> None:
    # The uniqueness check once scanned every player per seed, so 20,000
    # seeds took about 9 s. A set lookup per seed takes well under 1 s.
    seeds = [
        Player(f"syn-{i}", {}, account_user_id=f"acct-{i}", external_ids={"src": f"x{i}"})
        for i in range(20_000)
    ]
    start = time.perf_counter()
    engine = replay([], seeds, cfg=CFG, as_of=AS_OF)
    assert time.perf_counter() - start < 3.0
    assert len(engine.players) == 20_000
