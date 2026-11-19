"""More seed provenance coverage for issue #74.

The wrong-type provenance rules hold in every seed entry point, and every
state the engine makes with external provenance (a reseed, two formats
with one pair, a category state derived from an imported seed) loads
back from JSON. All ids are synthetic.
"""

from __future__ import annotations

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
    RatingEngine,
    RatingFormat,
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


def _seeded() -> RatingEngine:
    eng = RatingEngine(CFG)
    snap = ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="X")
    eng.seed_external_rating("syn-a", D, snap)
    return eng


ENTRY_POINTS: list[tuple[str, Callable[[list[Player]], object]]] = [
    ("replay", lambda seeds: replay(LEDGER, seeds, cfg=CFG, as_of=AS_OF)),
    ("live", lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="live")),
    (
        "scheduled",
        lambda seeds: prequential_predictions(LEDGER, seeds, cfg=CFG, mode="scheduled"),
    ),
]

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
    "call", [row[1] for row in ENTRY_POINTS], ids=[row[0] for row in ENTRY_POINTS]
)
@pytest.mark.parametrize(
    ("changes", "message"), [row[1:] for row in TYPE_ROWS], ids=[row[0] for row in TYPE_ROWS]
)
def test_batch_rejects_wrong_provenance_type(
    changes: dict[str, Any], message: str, call: Callable[[list[Player]], object]
) -> None:
    state = replace(_seeded().state("syn-a", D), **changes)
    seed = Player("syn-b", {D: state}, external_ids={"ext": "X"})
    with pytest.raises(InvalidSeedError, match=message):
        call([seed])


def _round_trip(eng: RatingEngine) -> RatingEngine:
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())), cfg=CFG)
    assert loaded.players == eng.players
    return loaded


def test_reseed_without_external_id_round_trips() -> None:
    eng = _seeded()
    eng.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot("ext", 4.2, CAPTURED), replace_unplayed_seed=True
    )
    assert eng.state("syn-a", D).initial_external_player_id is None
    assert eng.player("syn-a").external_ids == {"ext": "X"}
    _round_trip(eng)


def test_same_pair_in_two_formats_round_trips() -> None:
    eng = _seeded()
    snap = ExternalRatingSnapshot("ext", 4.1, CAPTURED, external_player_id="X")
    eng.seed_external_rating("syn-a", S, snap)
    loaded = _round_trip(eng)
    assert loaded.state("syn-a", S).initial_external_player_id == "X"
    assert loaded.state("syn-a", D).initial_external_player_id == "X"


def test_category_state_derived_from_imported_seed_round_trips() -> None:
    seeded = _seeded()
    # The engine stores no category starting state (issue #112); a seed can.
    seed = seeded.players["syn-a"]
    seed.ratings[MX] = seeded.state("syn-a", MX)
    eng = RatingEngine(CFG)
    eng.add_player(seed)
    derived = eng.players["syn-a"].ratings[MX]
    assert derived.initial_source == "open_parent:doubles"
    assert derived.initial_external_player_id is None
    _round_trip(eng)


def test_from_snapshot_rejects_empty_initial_source() -> None:
    raw = json.loads(json.dumps(_seeded().to_snapshot()))
    raw["players"][0]["ratings"]["doubles"]["initial_source"] = ""
    message = r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_source must not be blank$"
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(raw, cfg=CFG)
