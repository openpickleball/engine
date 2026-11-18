"""A seed's external provenance must be its own and not blank (issue #74).

A rating state that names an external id must have that (source, id) pair
in the same player's external_ids, so the identity uniqueness check of
issue #52 covers it. A blank or whitespace-only initial_source or
initial_external_player_id is rejected in every seed entry point, in
seed_external_rating, and in snapshot loading. All ids are synthetic.
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
from open_engine.engine import clone_player

D = RatingFormat.DOUBLES
CFG = Config()
CAPTURED = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)
LEDGER = [Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]

NOT_LINKED = (
    r"^Seed for syn-b in doubles: initial_external_player_id must be linked "
    r"in external_ids under initial_source$"
)
EXTERNAL_TAKEN = r"^This external identity is already linked to another player$"


def _seeded() -> RatingEngine:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="X")
    eng.seed_external_rating("syn-a", D, snap)
    return eng


def _state() -> RatingState:
    return _seeded().state("syn-a", D)


def _a_seed() -> Player:
    return clone_player(_seeded().player("syn-a"))


def _provisional() -> RatingState:
    return RatingEngine(CFG).seed_provisional_rating("syn-x", D)


# Each entry point takes a list of seeds and raises or returns.
ENTRY_POINTS: list[tuple[str, Callable[[list[Player]], object]]] = [
    ("replay", lambda seeds: replay(LEDGER, seeds, cfg=CFG, as_of=AS_OF)),
    ("live", lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="live")),
    (
        "scheduled",
        lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="scheduled"),
    ),
]
ENTRY_IDS = [row[0] for row in ENTRY_POINTS]
ENTRY_CALLS = [row[1] for row in ENTRY_POINTS]


# -- another player's identity, not listed: InvalidSeedError -----------------


def test_add_player_rejects_unlisted_external_provenance() -> None:
    eng = _seeded()
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match=NOT_LINKED):
        eng.add_player(Player("syn-b", {D: replace(_state())}))
    assert "syn-b" not in eng.players
    assert eng.to_snapshot() == before


@pytest.mark.parametrize("call", ENTRY_CALLS, ids=ENTRY_IDS)
def test_batch_rejects_unlisted_external_provenance(
    call: Callable[[list[Player]], object],
) -> None:
    b_seed = Player("syn-b", {D: replace(_state())})
    with pytest.raises(InvalidSeedError, match=NOT_LINKED):
        call([_a_seed(), b_seed])


# -- another player's identity, listed: PlayerError ---------------------------


def _listed_b() -> Player:
    return Player("syn-b", {D: replace(_state())}, external_ids={"ext": "X"})


def test_add_player_rejects_listed_identity_held_by_another_player() -> None:
    eng = _seeded()
    before = eng.to_snapshot()
    with pytest.raises(PlayerError, match=EXTERNAL_TAKEN):
        eng.add_player(_listed_b())
    assert eng.to_snapshot() == before


@pytest.mark.parametrize("call", ENTRY_CALLS, ids=ENTRY_IDS)
def test_batch_rejects_listed_identity_held_by_earlier_seed(
    call: Callable[[list[Player]], object],
) -> None:
    with pytest.raises(PlayerError, match=EXTERNAL_TAKEN):
        call([_a_seed(), _listed_b()])


# -- own identity: accepted ---------------------------------------------------


def _own_b() -> Player:
    state = replace(_state(), initial_external_player_id="Y")
    return Player("syn-b", {D: state}, external_ids={"ext": "Y"})


def test_add_player_accepts_own_external_provenance() -> None:
    eng = _seeded()
    eng.add_player(_own_b())
    assert eng.player("syn-b").external_ids == {"ext": "Y"}
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())), cfg=CFG)
    assert loaded.players == eng.players


@pytest.mark.parametrize("call", ENTRY_CALLS, ids=ENTRY_IDS)
def test_batch_accepts_own_external_provenance(call: Callable[[list[Player]], object]) -> None:
    call([_a_seed(), _own_b()])


def test_replay_with_own_external_provenance_keeps_both_players() -> None:
    engine = replay(LEDGER, [_a_seed(), _own_b()], cfg=CFG, as_of=AS_OF)
    assert engine.player("syn-a").external_ids == {"ext": "X"}
    assert engine.player("syn-b").external_ids == {"ext": "Y"}


# -- mismatch inside one player: InvalidSeedError ----------------------------


MISMATCH_ROWS: list[tuple[str, dict[str, str]]] = [
    ("other_id_same_source", {"ext": "Y"}),
    ("same_id_other_source", {"other": "X"}),
]


@pytest.mark.parametrize(
    "external_ids", [row[1] for row in MISMATCH_ROWS], ids=[row[0] for row in MISMATCH_ROWS]
)
def test_add_player_rejects_mismatched_provenance(external_ids: dict[str, str]) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(InvalidSeedError, match=NOT_LINKED):
        eng.add_player(Player("syn-b", {D: replace(_state())}, external_ids=external_ids))
    assert eng.players == {}


@pytest.mark.parametrize("call", ENTRY_CALLS, ids=ENTRY_IDS)
@pytest.mark.parametrize(
    "external_ids", [row[1] for row in MISMATCH_ROWS], ids=[row[0] for row in MISMATCH_ROWS]
)
def test_batch_rejects_mismatched_provenance(
    external_ids: dict[str, str], call: Callable[[list[Player]], object]
) -> None:
    with pytest.raises(InvalidSeedError, match=NOT_LINKED):
        call([Player("syn-b", {D: replace(_state())}, external_ids=external_ids)])


# -- blank provenance: InvalidSeedError ---------------------------------------

SOURCE_BLANK = r"^Seed for syn-b in doubles: initial_source must not be blank$"
ID_BLANK = r"^Seed for syn-b in doubles: initial_external_player_id must not be blank$"


def _blank_rows() -> list[tuple[str, Player, str]]:
    return [
        (
            "source_empty",
            Player("syn-b", {D: replace(_state(), initial_source="")}, external_ids={"ext": "X"}),
            SOURCE_BLANK,
        ),
        (
            "source_space",
            Player("syn-b", {D: replace(_state(), initial_source="  ")}, external_ids={"ext": "X"}),
            SOURCE_BLANK,
        ),
        (
            "id_empty",
            Player("syn-b", {D: replace(_state(), initial_external_player_id="")}),
            ID_BLANK,
        ),
        (
            "id_space",
            Player("syn-b", {D: replace(_state(), initial_external_player_id=" ")}),
            ID_BLANK,
        ),
        (
            "provisional_source_space",
            Player("syn-b", {D: replace(_provisional(), initial_source="  ")}),
            SOURCE_BLANK,
        ),
    ]


BLANK_ROWS = _blank_rows()
BLANK_IDS = [row[0] for row in BLANK_ROWS]
BLANK_PARAMS = [row[1:] for row in BLANK_ROWS]


@pytest.mark.parametrize(("seed", "message"), BLANK_PARAMS, ids=BLANK_IDS)
def test_add_player_rejects_blank_provenance(seed: Player, message: str) -> None:
    eng = RatingEngine(CFG)
    with pytest.raises(InvalidSeedError, match=message):
        eng.add_player(copy.deepcopy(seed))
    assert eng.players == {}


@pytest.mark.parametrize("call", ENTRY_CALLS, ids=ENTRY_IDS)
@pytest.mark.parametrize(("seed", "message"), BLANK_PARAMS, ids=BLANK_IDS)
def test_batch_rejects_blank_provenance(
    seed: Player, message: str, call: Callable[[list[Player]], object]
) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        call([copy.deepcopy(seed)])


# -- wrong types: InvalidSeedError --------------------------------------------

TYPE_ROWS: list[tuple[str, dict[str, Any], str]] = [
    (
        "source_int",
        {"initial_source": 3},
        r"^Seed for syn-b in doubles: initial_source must be a str$",
    ),
    (
        "id_int",
        {"initial_external_player_id": 3},
        r"^Seed for syn-b in doubles: initial_external_player_id must be a str or None$",
    ),
]


@pytest.mark.parametrize(
    ("changes", "message"), [row[1:] for row in TYPE_ROWS], ids=[row[0] for row in TYPE_ROWS]
)
def test_add_player_rejects_wrong_provenance_type(changes: dict[str, Any], message: str) -> None:
    eng = RatingEngine(CFG)
    seed = Player("syn-b", {D: replace(_state(), **changes)}, external_ids={"ext": "X"})
    with pytest.raises(InvalidSeedError, match=message):
        eng.add_player(seed)
    assert eng.players == {}


# -- seed_external_rating -----------------------------------------------------

SNAP_SOURCE_BLANK = r"^External snapshot source must not be blank$"
SNAP_ID_BLANK = r"^External snapshot external_player_id must not be blank$"

SEED_EXTERNAL_ROWS: list[tuple[str, ExternalRatingSnapshot, str]] = [
    ("source_empty_no_id", ExternalRatingSnapshot("", 4.0, CAPTURED), SNAP_SOURCE_BLANK),
    ("source_space_no_id", ExternalRatingSnapshot("  ", 4.0, CAPTURED), SNAP_SOURCE_BLANK),
    (
        "source_empty_with_id",
        ExternalRatingSnapshot("", 4.0, CAPTURED, external_player_id="X"),
        SNAP_SOURCE_BLANK,
    ),
    (
        "source_space_with_id",
        ExternalRatingSnapshot("  ", 4.0, CAPTURED, external_player_id="X"),
        SNAP_SOURCE_BLANK,
    ),
    (
        "id_empty",
        ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id=""),
        SNAP_ID_BLANK,
    ),
    (
        "id_space",
        ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="  "),
        SNAP_ID_BLANK,
    ),
]


@pytest.mark.parametrize(
    ("snap", "message"),
    [row[1:] for row in SEED_EXTERNAL_ROWS],
    ids=[row[0] for row in SEED_EXTERNAL_ROWS],
)
def test_seed_external_rating_rejects_blank(snap: ExternalRatingSnapshot, message: str) -> None:
    eng = RatingEngine(CFG)
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match=message):
        eng.seed_external_rating("syn-c", D, snap)
    assert eng.players == {}
    assert eng.to_snapshot() == before


def test_seed_external_rating_without_external_id_links_nothing() -> None:
    eng = RatingEngine(CFG)
    state = eng.seed_external_rating("syn-c", D, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    assert state.initial_external_player_id is None
    assert eng.player("syn-c").external_ids == {}


# -- snapshots ----------------------------------------------------------------


def _json(eng: RatingEngine) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(eng.to_snapshot()))
    return loaded


def test_seeded_snapshot_round_trips() -> None:
    eng = _seeded()
    assert RatingEngine.from_snapshot(_json(eng), cfg=CFG).players == eng.players


def test_played_and_replayed_snapshot_round_trips() -> None:
    ledger = [Match("m1", ("syn-a", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]
    ledger.append(
        Match("m2", ("syn-a", "syn-r"), ("syn-q", "syn-s"), ((11, 9),), date(2026, 10, 6), D)
    )
    engine = replay(ledger, [_a_seed()], cfg=CFG, as_of=AS_OF)
    assert engine.state("syn-a", D).matches_played == 2
    assert engine.state("syn-a", D).initial_external_player_id == "X"
    assert RatingEngine.from_snapshot(_json(engine), cfg=CFG).players == engine.players


def test_live_played_state_keeps_provenance_and_loads() -> None:
    eng = _seeded()
    eng.record(Match("m1", ("syn-a", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), AS_OF, D))
    assert eng.state("syn-a", D).initial_external_player_id == "X"
    assert RatingEngine.from_snapshot(_json(eng), cfg=CFG).players == eng.players


STATE_PATH = r"snapshot\.players\[0\]\.ratings\.doubles"

TAMPER_ROWS: list[tuple[str, dict[str, Any], dict[str, Any] | None, str]] = [
    (
        "source_space",
        {"initial_source": "  "},
        None,
        rf"^{STATE_PATH}\.initial_source must not be blank$",
    ),
    (
        "id_empty",
        {"initial_external_player_id": ""},
        None,
        rf"^{STATE_PATH}\.initial_external_player_id must be null or not blank$",
    ),
    (
        "id_space",
        {"initial_external_player_id": " "},
        None,
        rf"^{STATE_PATH}\.initial_external_player_id must be null or not blank$",
    ),
    (
        "external_ids_empty",
        {},
        {},
        rf"^{STATE_PATH}\.initial_external_player_id must be linked in "
        r"snapshot\.players\[0\]\.external_ids under initial_source$",
    ),
    (
        "external_ids_other_id",
        {},
        {"ext": "Z"},
        rf"^{STATE_PATH}\.initial_external_player_id must be linked in "
        r"snapshot\.players\[0\]\.external_ids under initial_source$",
    ),
    (
        "state_other_source",
        {"initial_source": "other"},
        None,
        rf"^{STATE_PATH}\.initial_external_player_id must be linked in "
        r"snapshot\.players\[0\]\.external_ids under initial_source$",
    ),
]


@pytest.mark.parametrize(
    ("state_changes", "external_ids", "message"),
    [row[1:] for row in TAMPER_ROWS],
    ids=[row[0] for row in TAMPER_ROWS],
)
def test_from_snapshot_rejects_tampered_provenance(
    state_changes: dict[str, Any], external_ids: dict[str, Any] | None, message: str
) -> None:
    raw = _json(_seeded())
    raw["players"][0]["ratings"]["doubles"].update(state_changes)
    if external_ids is not None:
        raw["players"][0]["external_ids"] = external_ids
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(raw, cfg=CFG)


def test_from_snapshot_rejects_second_player_naming_another_players_identity() -> None:
    raw = _json(_seeded())
    other = copy.deepcopy(raw["players"][0])
    other["id"] = "syn-b"
    other["external_ids"] = {}
    raw["players"].append(other)
    message = (
        r"^snapshot\.players\[1\]\.ratings\.doubles\.initial_external_player_id must be "
        r"linked in snapshot\.players\[1\]\.external_ids under initial_source$"
    )
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(raw, cfg=CFG)


def test_replay_from_tampered_snapshot_raises() -> None:
    checkpoint = replay([], [_a_seed()], cfg=CFG, as_of=AS_OF)
    raw = _json(checkpoint)
    raw["players"][0]["external_ids"] = {}
    with pytest.raises(SnapshotError, match=r"must be linked in"):
        replay([], [], cfg=CFG, as_of=AS_OF, from_snapshot=raw)
