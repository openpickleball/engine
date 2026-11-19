"""The open_ provenance prefix is reserved for the engine (issue #97).

An external source that starts with open_ is rejected wherever it can
enter: seed_external_rating, attach_external_id, seeds, and snapshots. A
seed or snapshot state may carry a reserved initial_source only in one of
the two shapes the engine writes. A blank evidence reference is rejected.
All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

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
    SnapshotError,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
CFG = Config()
CAPTURED = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)
LEDGER = [Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]
RESERVED = ["open_parent:doubles", "open_provisional", "open_x", "open_"]


def _json(eng: RatingEngine) -> dict[str, Any]:
    snap: dict[str, Any] = json.loads(json.dumps(eng.to_snapshot()))
    return snap


def _imported(**changes: Any) -> RatingState:
    """An unplayed imported doubles state with no external id, changed as given."""
    eng = RatingEngine(CFG)
    eng.seed_external_rating("syn-z", D, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    return replace(eng.state("syn-z", D), **changes)


def _provisional(**changes: Any) -> RatingState:
    """An unplayed provisional state, changed as given."""
    return replace(RatingState(rating=CFG.default_rating, rd=CFG.new_player_rd), **changes)


ENTRY_POINTS: list[tuple[str, Callable[[list[Player]], object]]] = [
    ("add_player", lambda seeds: RatingEngine(CFG).add_player(seeds[0])),
    ("replay", lambda seeds: replay(LEDGER, seeds, cfg=CFG, as_of=AS_OF)),
    ("live", lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="live")),
    (
        "scheduled",
        lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="scheduled"),
    ),
]
CALLS = pytest.mark.parametrize(
    "call", [row[1] for row in ENTRY_POINTS], ids=[row[0] for row in ENTRY_POINTS]
)

RESERVED_MESSAGE = "uses the reserved prefix open_ but is not provenance the engine writes"


# -- criterion 1: seed_external_rating ----------------------------------------


@pytest.mark.parametrize("source", RESERVED)
def test_seed_external_rating_rejects_a_reserved_source(source: str) -> None:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot(source, 4.0, CAPTURED, external_player_id="syn-q")
    with pytest.raises(
        InvalidSeedError, match=r"^External snapshot source must not start with open_$"
    ):
        eng.seed_external_rating("syn-a", MX, snap)
    assert eng.players == {}


def test_seed_external_rating_reserved_source_changes_nothing_on_an_existing_player() -> None:
    eng = RatingEngine(CFG)
    eng.add_player(Player("syn-a", {MX: _provisional()}))
    before = eng.to_snapshot()
    snap = ExternalRatingSnapshot("open_parent:doubles", 4.0, CAPTURED)
    with pytest.raises(InvalidSeedError, match="must not start with open_"):
        eng.seed_external_rating("syn-a", MX, snap, replace_unplayed_seed=True)
    assert eng.to_snapshot() == before


def test_seed_external_rating_checks_blank_before_the_prefix() -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(InvalidSeedError, match=r"^External snapshot source must not be blank$"):
        eng.seed_external_rating("syn-a", D, ExternalRatingSnapshot("  ", 4.0, CAPTURED))


@pytest.mark.parametrize("source", ["OPEN_x", " open_x", "opened", "ext:open_x"])
def test_seed_external_rating_accepts_a_source_without_the_exact_prefix(source: str) -> None:
    eng = RatingEngine(CFG)
    state = eng.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot(source, 4.0, CAPTURED, external_player_id="syn-q")
    )
    assert state.initial_source == source
    assert eng.player("syn-a").external_ids == {source: "syn-q"}
    RatingEngine.from_snapshot(_json(eng), cfg=CFG)


# -- criterion 2: attach_external_id ------------------------------------------


@pytest.mark.parametrize("source", RESERVED)
def test_attach_external_id_rejects_a_reserved_source(source: str) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(PlayerError, match=r"^source must not start with open_$"):
        eng.attach_external_id("syn-b", source, "syn-z")
    assert eng.players == {}


def test_attach_external_id_reserved_source_changes_nothing_on_an_existing_player() -> None:
    eng = RatingEngine(CFG)
    eng.seed_provisional_rating("syn-b", D)
    before = eng.to_snapshot()
    with pytest.raises(PlayerError, match="must not start with open_"):
        eng.attach_external_id("syn-b", "open_provisional", "syn-z")
    assert eng.to_snapshot() == before


# -- criteria 3 and 6: seeds --------------------------------------------------


SEED_ROWS: list[tuple[str, Player, str]] = [
    (
        "external_ids_key",
        Player("syn-b", external_ids={"open_x": "syn-w"}),
        r"^Seed for syn-b: external_ids source must not start with open_$",
    ),
    (
        "dated_provisional",
        Player("syn-b", {D: _imported(initial_source="open_provisional")}),
        r"^Seed for syn-b in doubles: initial_source 'open_provisional' " + RESERVED_MESSAGE,
    ),
    (
        "parent_in_doubles",
        Player("syn-b", {D: _imported(initial_source="open_parent:doubles")}),
        r"^Seed for syn-b in doubles: initial_source 'open_parent:doubles' " + RESERVED_MESSAGE,
    ),
    (
        "parent_in_singles",
        Player("syn-b", {S: _imported(initial_source="open_parent:doubles")}),
        r"^Seed for syn-b in singles: initial_source 'open_parent:doubles' " + RESERVED_MESSAGE,
    ),
    (
        "wrong_parent",
        Player("syn-b", {MX: _imported(initial_source="open_parent:singles")}),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:singles' "
        + RESERVED_MESSAGE,
    ),
    (
        "parent_with_reliability",
        Player(
            "syn-b",
            {MX: _imported(initial_source="open_parent:doubles", initial_source_reliability=50)},
        ),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "parent_with_external_id",
        Player(
            "syn-b",
            {
                MX: _imported(
                    initial_source="open_parent:doubles", initial_external_player_id="syn-q"
                )
            },
        ),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "parent_with_evidence",
        Player(
            "syn-b",
            {MX: _imported(initial_source="open_parent:doubles", initial_evidence_reference="r")},
        ),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "provisional_with_evidence",
        Player("syn-b", {D: _provisional(initial_evidence_reference="r")}),
        r"^Seed for syn-b in doubles: initial_source 'open_provisional' " + RESERVED_MESSAGE,
    ),
    (
        "other_reserved",
        Player("syn-b", {D: _provisional(initial_source="open_x")}),
        r"^Seed for syn-b in doubles: initial_source 'open_x' " + RESERVED_MESSAGE,
    ),
    (
        "derived_undated",
        Player("syn-b", {MX: _provisional(initial_source="open_parent:doubles")}),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "derived_dated",
        Player(
            "syn-b",
            {
                MX: _imported(
                    initial_source="open_parent:doubles",
                    rd=max(CFG.external_seed_rd, CFG.category_seed_rd),
                )
            },
        ),
        r"^Seed for syn-b in mixed_doubles: initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "blank_evidence",
        Player("syn-b", {D: _imported(initial_evidence_reference="  ")}),
        r"^Seed for syn-b in doubles: initial_evidence_reference must not be blank$",
    ),
    (
        "empty_evidence",
        Player("syn-b", {D: _imported(initial_evidence_reference="")}),
        r"^Seed for syn-b in doubles: initial_evidence_reference must not be blank$",
    ),
]


@CALLS
@pytest.mark.parametrize(
    ("seed", "message"), [row[1:] for row in SEED_ROWS], ids=[row[0] for row in SEED_ROWS]
)
def test_seed_rejects_reserved_provenance(
    seed: Player, message: str, call: Callable[[list[Player]], object]
) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        call([seed])


def test_add_player_reserved_provenance_changes_nothing() -> None:
    eng = RatingEngine(CFG)
    eng.seed_provisional_rating("syn-a", D)
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match="reserved prefix open_"):
        eng.add_player(Player("syn-b", {D: _provisional(initial_source="open_x")}))
    assert eng.to_snapshot() == before


# -- criterion 4: the engine's own shapes are still accepted ------------------


def _derived(dated: bool) -> RatingState:
    if dated:
        return _imported(
            initial_source="open_parent:doubles",
            rd=max(CFG.external_seed_rd, CFG.category_seed_rd),
        )
    return _provisional(initial_source="open_parent:doubles")


ACCEPT_ROWS: list[tuple[str, Player]] = [
    ("provisional", Player("syn-b", {D: _provisional()})),
    ("derived_dated_with_parent", Player("syn-b", {D: _imported(), MX: _derived(dated=True)})),
    ("non_reserved_case", Player("syn-b", {D: _imported()}, external_ids={"OPEN_x": "syn-w"})),
    ("evidence_set", Player("syn-b", {D: _imported(initial_evidence_reference="ref-1")})),
]


@CALLS
@pytest.mark.parametrize(
    "seed", [row[1] for row in ACCEPT_ROWS], ids=[row[0] for row in ACCEPT_ROWS]
)
def test_seed_accepts_engine_shapes(seed: Player, call: Callable[[list[Player]], object]) -> None:
    call([seed])


@pytest.mark.parametrize(
    "seed", [row[1] for row in ACCEPT_ROWS], ids=[row[0] for row in ACCEPT_ROWS]
)
def test_accepted_seed_round_trips(seed: Player) -> None:
    eng = RatingEngine(CFG)
    eng.add_player(seed)
    loaded = RatingEngine.from_snapshot(_json(eng), cfg=CFG)
    assert loaded.players == eng.players


# -- criteria 5 and 6: snapshots ----------------------------------------------


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


def _engine_holding(state: RatingState, fmt: RatingFormat) -> RatingEngine:
    """An engine whose syn-b holds state, set directly so no seed check runs."""
    eng = RatingEngine(CFG)
    eng.add_player(Player("syn-b", {fmt: _provisional()}))
    eng.players["syn-b"].ratings[fmt] = state
    return eng


SNAP_ROWS: list[tuple[str, RatingFormat, RatingState, str]] = [
    (
        "dated_provisional",
        D,
        _imported(initial_source="open_provisional"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_source 'open_provisional' "
        + RESERVED_MESSAGE,
    ),
    (
        "parent_in_doubles",
        D,
        _imported(initial_source="open_parent:doubles"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_source 'open_parent:doubles' "
        + RESERVED_MESSAGE,
    ),
    (
        "parent_with_reliability",
        MX,
        _imported(initial_source="open_parent:doubles", initial_source_reliability=50),
        r"^snapshot\.players\[0\]\.ratings\.mixed_doubles\.initial_source "
        r"'open_parent:doubles' " + RESERVED_MESSAGE,
    ),
    (
        "parent_with_evidence",
        MX,
        _imported(initial_source="open_parent:doubles", initial_evidence_reference="r"),
        r"^snapshot\.players\[0\]\.ratings\.mixed_doubles\.initial_source "
        r"'open_parent:doubles' " + RESERVED_MESSAGE,
    ),
    (
        "other_reserved",
        D,
        _provisional(initial_source="open_x"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_source 'open_x' " + RESERVED_MESSAGE,
    ),
    (
        "blank_evidence",
        D,
        _imported(initial_evidence_reference="  "),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_evidence_reference must be null "
        "or not blank$",
    ),
]


@pytest.mark.parametrize(
    ("fmt", "state", "message"),
    [row[1:] for row in SNAP_ROWS],
    ids=[row[0] for row in SNAP_ROWS],
)
def test_from_snapshot_rejects_reserved_provenance(
    fmt: RatingFormat, state: RatingState, message: str
) -> None:
    eng = RatingEngine(CFG)
    eng.add_player(Player("syn-b", {fmt: _provisional()}))
    raw = _json(eng)
    raw["players"][0]["ratings"][fmt.value] = _state_form(state)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(raw, cfg=CFG)
    with pytest.raises(SnapshotError, match=message):
        replay(LEDGER, cfg=CFG, as_of=AS_OF, from_snapshot=raw)


@pytest.mark.parametrize(
    ("fmt", "state", "message"),
    [row[1:] for row in SNAP_ROWS],
    ids=[row[0] for row in SNAP_ROWS],
)
def test_to_snapshot_rejects_reserved_provenance(
    fmt: RatingFormat, state: RatingState, message: str
) -> None:
    eng = _engine_holding(state, fmt)
    with pytest.raises(SnapshotError, match=message):
        eng.to_snapshot()


def test_from_snapshot_rejects_a_reserved_external_ids_key() -> None:
    eng = RatingEngine(CFG)
    eng.attach_external_id("syn-b", "ext", "syn-w")
    raw = _json(eng)
    raw["players"][0]["external_ids"] = {"open_x": "syn-w"}
    message = r"^snapshot\.players\[0\]\.external_ids source must not start with open_$"
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(raw, cfg=CFG)


def test_to_snapshot_rejects_a_reserved_external_ids_key() -> None:
    eng = RatingEngine(CFG)
    eng.attach_external_id("syn-b", "ext", "syn-w")
    eng.players["syn-b"].external_ids["open_x"] = "syn-v"
    with pytest.raises(SnapshotError, match="external_ids source must not start with open_"):
        eng.to_snapshot()


# -- criterion 6: evidence reference in seed_external_rating ------------------


@pytest.mark.parametrize("reference", ["", "  ", "\t"])
def test_seed_external_rating_rejects_a_blank_evidence_reference(reference: str) -> None:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot("ext", 4.0, CAPTURED, evidence_reference=reference)
    with pytest.raises(
        InvalidSeedError, match=r"^External snapshot evidence_reference must not be blank$"
    ):
        eng.seed_external_rating("syn-a", D, snap)
    assert eng.players == {}


def test_seed_external_rating_accepts_a_missing_or_repeated_evidence_reference() -> None:
    eng = RatingEngine(CFG)
    eng.seed_external_rating("syn-a", D, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    for player_id in ("syn-b", "syn-c"):
        eng.seed_external_rating(
            player_id, D, ExternalRatingSnapshot("ext", 4.0, CAPTURED, evidence_reference="r-1")
        )
    assert eng.state("syn-c", D).initial_evidence_reference == "r-1"


# -- criterion 7: every engine-produced snapshot still loads ------------------


def test_engine_paths_round_trip() -> None:
    eng = RatingEngine(CFG)
    snap_a = ExternalRatingSnapshot(
        "ext", 4.0, CAPTURED, 70, external_player_id="syn-xa", evidence_reference="ref-a"
    )
    # Under category_feeds_parent the engine stores no derived category
    # state (issue #112); a seed can hold one.
    eng.add_player(derived_seed("syn-a", snap_a, MX, CFG))
    derived = eng.players["syn-a"].ratings[MX]
    assert derived.initial_source == "open_parent:doubles"
    assert derived.initial_source_date == CAPTURED
    eng.add_player(Player("syn-b", {D: _provisional()}))
    eng.attach_external_id("syn-b", "ext", "syn-xb")
    eng.add_player(Player("syn-c", {MX: _provisional()}))
    match = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 5), D)
    assert eng.record(match).counted
    snap = json.loads(json.dumps(eng.to_snapshot()))
    assert RatingEngine.from_snapshot(snap, cfg=CFG).to_snapshot() == eng.to_snapshot()


def test_replay_with_derived_category_round_trips() -> None:
    ledger = [
        Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 5), D),
        Match("m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 9),), date(2026, 10, 6), MX),
    ]
    eng = replay(ledger, cfg=CFG, as_of=AS_OF)
    assert eng.state("syn-a", MX).initial_source == "open_parent:doubles"
    snap = json.loads(json.dumps(eng.to_snapshot()))
    assert RatingEngine.from_snapshot(snap, cfg=CFG).to_snapshot() == eng.to_snapshot()


# -- criterion 8: the issue's reproduction ------------------------------------


def test_issue_reproduction_is_rejected() -> None:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot("open_parent:doubles", 4.0, CAPTURED, external_player_id="syn-q")
    with pytest.raises(InvalidSeedError, match="must not start with open_"):
        eng.seed_external_rating("syn-a", MX, snap)
    assert eng.players == {}


def test_reseed_of_an_engine_derived_state_is_still_refused() -> None:
    eng = RatingEngine(CFG)
    eng.add_player(derived_seed("syn-a", ExternalRatingSnapshot("ext", 4.0, CAPTURED), MX, CFG))
    assert MX in eng.players["syn-a"].ratings
    with pytest.raises(
        InvalidSeedError, match=r"^Cannot replace a rating derived from OPEN overall doubles$"
    ):
        eng.seed_external_rating(
            "syn-a", MX, ExternalRatingSnapshot("ext", 4.1, CAPTURED), replace_unplayed_seed=True
        )
