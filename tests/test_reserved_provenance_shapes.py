"""Reserved open_parent: provenance must match how the engine derives it (issue #97).

A category state named "open_parent:" plus its parent's value is accepted in
a seed or a snapshot only when category_feeds_parent is on, the same player
has a parent state, the state is dated exactly when the parent state is
(an undated one needs a played parent), and, while unplayed, its RD is at
least category_seed_rd. More entry point coverage for the reserved prefix.
All ids are synthetic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

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
    SnapshotError,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
CFG = Config()
OFF = Config(category_feeds_parent=False)
EARLY = date(2026, 9, 1)
CAPTURED = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)
LEDGER = [Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]
CATEGORY_RD = max(CFG.external_seed_rd, CFG.category_seed_rd)
RESERVED = "uses the reserved prefix open_ but is not provenance the engine writes"
NO_FEED = ": category_feeds_parent is off"
NO_PARENT = ": the player has no doubles state"
DATE_MISMATCH = ": initial_source_date must be set exactly when the doubles state's is"
UNPLAYED_PARENT = ": an undated derived state needs a played doubles state"
LOW_RD = ": rd must be at least category_seed_rd while the state is unplayed"
IMPORT_FIELDS = (
    ": it has an imported reliability, initial_external_player_id, or initial_evidence_reference"
)
WRONG_NAME = ": the engine writes no such initial_source in this format"


def _imported(on: date = CAPTURED, **changes: Any) -> RatingState:
    """An unplayed imported state with no external id, changed as given."""
    state = RatingState(
        rating=4.0,
        rd=CFG.external_seed_rd,
        rd_as_of=on,
        initial_rating=4.0,
        initial_source="ext",
        initial_source_date=on,
    )
    return replace(state, **changes)


def _provisional(**changes: Any) -> RatingState:
    """An unplayed provisional state, changed as given."""
    return replace(RatingState(rating=CFG.default_rating, rd=CFG.new_player_rd), **changes)


def _derived(on: date | None = CAPTURED, **changes: Any) -> RatingState:
    """An unplayed open_parent:doubles state, dated or not, changed as given."""
    if on is None:
        return _provisional(**{"initial_source": "open_parent:doubles", **changes})
    return _imported(on, **{"initial_source": "open_parent:doubles", "rd": CATEGORY_RD, **changes})


def _calls(cfg: Config) -> list[tuple[str, Callable[[list[Player]], object]]]:
    return [
        ("add_player", lambda seeds: RatingEngine(cfg).add_player(seeds[0])),
        ("replay", lambda seeds: replay(LEDGER, seeds, cfg=cfg, as_of=AS_OF)),
        ("live", lambda seeds: prequential_predictions(LEDGER, seeds, cfg=cfg, mode="live")),
        (
            "scheduled",
            lambda seeds: prequential_predictions(LEDGER, seeds, cfg=cfg, mode="scheduled"),
        ),
    ]


ENTRY_NAMES = ["add_player", "replay", "live", "scheduled"]


def _call(cfg: Config, name: str) -> Callable[[list[Player]], object]:
    return dict(_calls(cfg))[name]


def _msg(fmt: str, suffix: str) -> str:
    return rf"^Seed for syn-b in {fmt}: initial_source 'open_parent:doubles' {RESERVED}{suffix}$"


# -- F1: seeds ----------------------------------------------------------------


REJECT_ROWS: list[tuple[str, Config, dict[RatingFormat, RatingState], str]] = [
    (
        "undated_no_parent_any_rating",
        CFG,
        {MX: _derived(None, rating=6.5, initial_rating=6.5)},
        NO_PARENT,
    ),
    ("dated_no_parent", CFG, {MX: _derived()}, NO_PARENT),
    ("feeds_parent_off", OFF, {D: _imported(), MX: _derived()}, NO_FEED),
    (
        "feeds_parent_off_undated",
        OFF,
        {MX: _derived(None, rating=6.5, initial_rating=6.5)},
        NO_FEED,
    ),
    ("dated_low_rd", CFG, {D: _imported(), MX: _derived(rd=CFG.external_seed_rd)}, LOW_RD),
    ("dated_child_undated_parent", CFG, {D: _provisional(), MX: _derived()}, DATE_MISMATCH),
    ("undated_child_dated_parent", CFG, {D: _imported(), MX: _derived(None)}, DATE_MISMATCH),
    (
        "undated_child_unplayed_parent",
        CFG,
        {D: _provisional(), MX: _derived(None)},
        UNPLAYED_PARENT,
    ),
]


@pytest.mark.parametrize("entry", ENTRY_NAMES)
@pytest.mark.parametrize(
    ("cfg", "ratings", "suffix"),
    [row[1:] for row in REJECT_ROWS],
    ids=[row[0] for row in REJECT_ROWS],
)
def test_seed_rejects_open_parent_shapes_the_engine_cannot_derive(
    cfg: Config, ratings: dict[RatingFormat, RatingState], suffix: str, entry: str
) -> None:
    with pytest.raises(InvalidSeedError, match=_msg("mixed_doubles", suffix)):
        _call(cfg, entry)([Player("syn-b", dict(ratings))])


ACCEPT_ROWS: list[tuple[str, dict[RatingFormat, RatingState]]] = [
    ("same_dates", {D: _imported(), MX: _derived()}),
    ("child_older_than_parent", {D: _imported(CAPTURED), MX: _derived(EARLY)}),
    ("child_newer_than_parent", {D: _imported(EARLY), MX: _derived(CAPTURED)}),
]


@pytest.mark.parametrize("entry", ENTRY_NAMES)
@pytest.mark.parametrize(
    "ratings", [row[1] for row in ACCEPT_ROWS], ids=[row[0] for row in ACCEPT_ROWS]
)
def test_seed_accepts_open_parent_shapes_the_engine_derives(
    ratings: dict[RatingFormat, RatingState], entry: str
) -> None:
    _call(CFG, entry)([Player("syn-b", dict(ratings))])


def test_add_player_open_parent_rejection_changes_nothing() -> None:
    eng = RatingEngine(CFG)
    eng.seed_provisional_rating("syn-a", D)
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match=_msg("mixed_doubles", NO_PARENT)):
        eng.add_player(Player("syn-b", {MX: _derived(None, rating=6.5, initial_rating=6.5)}))
    assert eng.to_snapshot() == before


def test_seed_cloned_from_a_feeding_engine_is_rejected_when_categories_do_not_feed() -> None:
    source = RatingEngine(CFG)
    source.seed_external_rating("syn-b", D, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    source.seed_provisional_rating("syn-b", MX)
    seed = copy.deepcopy(source.player("syn-b"))
    RatingEngine(CFG).add_player(seed)
    with pytest.raises(InvalidSeedError, match=_msg("mixed_doubles", NO_FEED)):
        RatingEngine(OFF).add_player(seed)


def test_parent_reseed_keeps_the_derived_state_loadable_and_seedable() -> None:
    eng = RatingEngine(CFG)
    eng.seed_external_rating("syn-b", D, ExternalRatingSnapshot("ext", 4.0, EARLY))
    eng.seed_provisional_rating("syn-b", MX)
    eng.seed_external_rating(
        "syn-b", D, ExternalRatingSnapshot("ext", 4.2, CAPTURED), replace_unplayed_seed=True
    )
    assert eng.state("syn-b", MX).initial_source_date == EARLY
    assert eng.state("syn-b", D).initial_source_date == CAPTURED
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())), cfg=CFG)
    assert loaded.to_snapshot() == eng.to_snapshot()
    RatingEngine(CFG).add_player(copy.deepcopy(eng.player("syn-b")))


def test_played_undated_parent_derivation_round_trips() -> None:
    eng = RatingEngine(CFG)
    assert eng.record(LEDGER[0]).counted
    derived = eng.seed_provisional_rating("syn-p", MX)
    assert derived.initial_source == "open_parent:doubles"
    assert derived.initial_source_date is None
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())), cfg=CFG)
    assert loaded.to_snapshot() == eng.to_snapshot()


# -- F4: a dated seed that never set initial_source ---------------------------


def test_dated_seed_with_the_default_initial_source_is_rejected() -> None:
    seed = Player(
        "syn-b",
        {
            D: RatingState(
                rating=4.0,
                rd=CFG.external_seed_rd,
                rd_as_of=CAPTURED,
                initial_rating=4.0,
                initial_source_date=CAPTURED,
            )
        },
    )
    with pytest.raises(
        InvalidSeedError,
        match=r"^Seed for syn-b in doubles: initial_source 'open_provisional' " + RESERVED,
    ):
        RatingEngine(CFG).add_player(seed)


# -- F1 and F5: snapshots -----------------------------------------------------


def _state_form(state: RatingState) -> dict[str, Any]:
    """The JSON form of a state, built without running any check."""
    form: dict[str, Any] = {}
    for name, value in vars(state).items():
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, dict):
            value = {k: v.isoformat() for k, v in value.items()}
        form[name] = value
    return form


def _snap_msg(fmt: str, source: str, suffix: str) -> str:
    return (
        rf"^snapshot\.players\[0\]\.ratings\.{fmt}\.initial_source '{source}' {RESERVED}{suffix}$"
    )


SNAP_ROWS: list[tuple[str, Config, dict[RatingFormat, RatingState], RatingFormat, str]] = [
    ("feeds_parent_off", OFF, {D: _imported(), MX: _derived()}, MX, NO_FEED),
    ("no_parent", CFG, {MX: _derived()}, MX, NO_PARENT),
    (
        "undated_no_parent_any_rating",
        CFG,
        {MX: _derived(None, rating=6.5, initial_rating=6.5)},
        MX,
        NO_PARENT,
    ),
    ("dated_child_undated_parent", CFG, {D: _provisional(), MX: _derived()}, MX, DATE_MISMATCH),
    ("undated_child_dated_parent", CFG, {D: _imported(), MX: _derived(None)}, MX, DATE_MISMATCH),
    (
        "undated_child_unplayed_parent",
        CFG,
        {D: _provisional(), MX: _derived(None)},
        MX,
        UNPLAYED_PARENT,
    ),
    ("dated_low_rd", CFG, {D: _imported(), MX: _derived(rd=CFG.external_seed_rd)}, MX, LOW_RD),
]


def _engine_holding(cfg: Config, ratings: dict[RatingFormat, RatingState]) -> RatingEngine:
    """An engine whose syn-b holds ratings, set directly so no seed check runs."""
    eng = RatingEngine(cfg)
    eng.add_player(Player("syn-b"))
    eng.players["syn-b"].ratings.update(ratings)
    return eng


def _raw_holding(cfg: Config, ratings: dict[RatingFormat, RatingState]) -> dict[str, Any]:
    eng = RatingEngine(cfg)
    eng.add_player(Player("syn-b"))
    raw: dict[str, Any] = json.loads(json.dumps(eng.to_snapshot()))
    raw["players"][0]["ratings"] = {fmt.value: _state_form(s) for fmt, s in ratings.items()}
    return raw


@pytest.mark.parametrize(
    ("cfg", "ratings", "fmt", "suffix"),
    [row[1:] for row in SNAP_ROWS],
    ids=[row[0] for row in SNAP_ROWS],
)
def test_snapshot_rejects_open_parent_shapes_the_engine_cannot_derive(
    cfg: Config, ratings: dict[RatingFormat, RatingState], fmt: RatingFormat, suffix: str
) -> None:
    message = _snap_msg(fmt.value, "open_parent:doubles", suffix)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(_raw_holding(cfg, ratings), cfg=cfg)
    with pytest.raises(SnapshotError, match=message):
        _engine_holding(cfg, ratings).to_snapshot()


EXTRA_SNAP_ROWS: list[tuple[str, dict[RatingFormat, RatingState], str]] = [
    (
        "parent_with_external_id",
        {D: _imported(), MX: _derived(initial_external_player_id="syn-q")},
        _snap_msg("mixed_doubles", "open_parent:doubles", IMPORT_FIELDS),
    ),
    (
        "parent_in_singles",
        {S: _imported(initial_source="open_parent:doubles")},
        _snap_msg("singles", "open_parent:doubles", WRONG_NAME),
    ),
    (
        "wrong_parent",
        {D: _imported(), MX: _derived(initial_source="open_parent:singles")},
        _snap_msg("mixed_doubles", "open_parent:singles", WRONG_NAME),
    ),
    (
        "empty_evidence",
        {D: _imported(initial_evidence_reference="")},
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_evidence_reference must be null "
        "or not blank$",
    ),
]


@pytest.mark.parametrize(
    ("ratings", "message"),
    [row[1:] for row in EXTRA_SNAP_ROWS],
    ids=[row[0] for row in EXTRA_SNAP_ROWS],
)
def test_snapshot_rejects_more_reserved_provenance(
    ratings: dict[RatingFormat, RatingState], message: str
) -> None:
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(_raw_holding(CFG, ratings), cfg=CFG)
    with pytest.raises(SnapshotError, match=message):
        _engine_holding(CFG, ratings).to_snapshot()


def _checkpoint() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(json.dumps(replay(LEDGER, cfg=CFG, as_of=AS_OF).to_snapshot()))
    assert raw["resumable"] is True
    return raw


RESUME_SEEDS: list[tuple[str, Player, str]] = [
    (
        "external_ids_key",
        Player("syn-b", external_ids={"open_x": "syn-w"}),
        r"^Seed for syn-b: external_ids source must not start with open_$",
    ),
    (
        "initial_source",
        Player("syn-b", {D: _provisional(initial_source="open_x")}),
        r"^Seed for syn-b in doubles: initial_source 'open_x' " + RESERVED,
    ),
]


@pytest.mark.parametrize(
    ("seed", "message"), [row[1:] for row in RESUME_SEEDS], ids=[row[0] for row in RESUME_SEEDS]
)
def test_resume_rejects_a_reserved_seed(seed: Player, message: str) -> None:
    snap = _checkpoint()
    before = copy.deepcopy(snap)
    with pytest.raises(InvalidSeedError, match=message):
        replay(LEDGER, [seed], cfg=CFG, as_of=AS_OF, from_snapshot=snap)
    assert snap == before


def test_resume_rejects_a_reserved_external_ids_key_in_the_snapshot() -> None:
    snap = _checkpoint()
    snap["players"][0]["external_ids"] = {"open_x": "syn-w"}
    with pytest.raises(
        SnapshotError,
        match=r"^snapshot\.players\[0\]\.external_ids source must not start with open_$",
    ):
        replay(LEDGER, cfg=CFG, as_of=AS_OF, from_snapshot=snap)


def test_reserved_external_ids_key_changes_nothing_on_an_engine_with_players() -> None:
    eng = RatingEngine(CFG)
    eng.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="syn-xa")
    )
    eng.seed_provisional_rating("syn-c", MX)
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match="external_ids source must not start with open_"):
        eng.add_player(Player("syn-b", external_ids={"open_x": "syn-w"}))
    with pytest.raises(PlayerError, match=r"^source must not start with open_$"):
        eng.attach_external_id("syn-a", "open_x", "syn-w")
    with pytest.raises(InvalidSeedError, match="External snapshot source must not start"):
        eng.seed_external_rating(
            "syn-a",
            S,
            ExternalRatingSnapshot("open_x", 4.0, CAPTURED, external_player_id="syn-w"),
        )
    assert eng.to_snapshot() == before


# -- reserved prefix with category_feeds_parent off ---------------------------


def test_reserved_prefix_is_rejected_when_categories_do_not_feed() -> None:
    eng = RatingEngine(OFF)
    with pytest.raises(InvalidSeedError, match="External snapshot source must not start"):
        eng.seed_external_rating("syn-a", MX, ExternalRatingSnapshot("open_x", 4.0, CAPTURED))
    with pytest.raises(PlayerError, match=r"^source must not start with open_$"):
        eng.attach_external_id("syn-a", "open_provisional", "syn-w")
    with pytest.raises(InvalidSeedError, match="external_ids source must not start with open_"):
        eng.add_player(Player("syn-b", external_ids={"open_x": "syn-w"}))
    assert eng.players == {}
    raw = _raw_holding(OFF, {D: _imported()})
    raw["players"][0]["external_ids"] = {"open_x": "syn-w"}
    with pytest.raises(SnapshotError, match="external_ids source must not start with open_"):
        RatingEngine.from_snapshot(raw, cfg=OFF)


def test_provisional_seed_is_still_accepted_when_categories_do_not_feed() -> None:
    eng = RatingEngine(OFF)
    eng.add_player(Player("syn-b", {MX: _provisional(rating=6.5, initial_rating=6.5)}))
    assert eng.state("syn-b", MX).initial_source == "open_provisional"
