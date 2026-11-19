"""Review fixes for the issue #23 addendum.

inherited_live_updates comes after opponents, so positional RatingState
calls keep their meaning. A snapshot state with a nonzero inherited count
must carry the provenance the engine gives a derived state. All ids are
synthetic.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from helpers import derived_seed, store_state
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
CUT = date(2025, 3, 1)
TEAMS = (("syn-a", "syn-b"), ("syn-c", "syn-d"))
HISTORY = [Match("syn-h1", *TEAMS, ((11, 4),), date(2025, 2, 1), D)]


# AD2


def test_positional_partners_still_land_in_partners() -> None:
    partners = {"syn-x": date(2025, 1, 1)}
    state = RatingState(
        3.5, 0.9, 0, None, None, 3.5, "open_provisional", None, None, None, None, 0, partners
    )
    assert state.partners == partners
    assert state.inherited_live_updates == 0


def test_inherited_live_updates_comes_after_opponents() -> None:
    # verified_matches_played (issue #59) was added after it, also at the end.
    names = [f.name for f in dataclasses.fields(RatingState)]
    assert names[-2:] == ["inherited_live_updates", "verified_matches_played"]
    assert names.index("inherited_live_updates") > names.index("opponents")


# AD4


def _live_engine(seeded_parent: bool = False) -> RatingEngine:
    seeds: list[Player] = []
    if seeded_parent:
        seeder = RatingEngine()
        seeder.seed_external_rating(
            "syn-a",
            D,
            ExternalRatingSnapshot("external_standard", 4.2, date(2025, 1, 1), 80.0, "ext-a"),
        )
        seeds = [seeder.players["syn-a"]]
    engine = replay(HISTORY, seeds, as_of=CUT)
    for i in range(2):
        match = Match(f"syn-l{i}", *TEAMS, ((11, 4),), date(2025, 3, 2 + i), D)
        assert engine.record(match).counted
    store_state(
        engine, "syn-a", MX
    )  # a state an earlier release's seed_provisional_rating stored (issue #112)
    return engine


def _json(engine: RatingEngine) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _ratings(snap: dict[str, Any]) -> dict[str, Any]:
    player = next(p for p in snap["players"] if p["id"] == "syn-a")
    ratings: dict[str, Any] = player["ratings"]
    return ratings


PROVENANCE = r"mixed_doubles: a state with inherited_live_updates must have"
SOURCE_DATE = r"mixed_doubles\.initial_source_date must equal the parent state's"

TAMPERED: list[tuple[str, bool, dict[str, object], str]] = [
    ("external player id", False, {"initial_external_player_id": "x1"}, PROVENANCE),
    ("evidence reference", False, {"initial_evidence_reference": "ev1"}, PROVENANCE),
    ("source date with no parent date", False, {"initial_source_date": "2025-01-01"}, SOURCE_DATE),
    ("reliability under a seeded parent", True, {"initial_source_reliability": 55.0}, PROVENANCE),
    ("other date under a seeded parent", True, {"initial_source_date": "2025-01-02"}, SOURCE_DATE),
]


@pytest.mark.parametrize(
    ("seeded_parent", "changes", "message"),
    [case[1:] for case in TAMPERED],
    ids=[case[0] for case in TAMPERED],
)
def test_a_derived_state_with_foreign_provenance_does_not_load(
    seeded_parent: bool, changes: dict[str, object], message: str
) -> None:
    snap = _json(_live_engine(seeded_parent))
    assert _ratings(snap)["mixed_doubles"]["inherited_live_updates"] == 2
    _ratings(snap)["mixed_doubles"].update(changes)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap)


@pytest.mark.parametrize("seeded_parent", [False, True], ids=["provisional", "seeded"])
def test_an_engine_made_derived_state_still_loads(seeded_parent: bool) -> None:
    snap = _json(_live_engine(seeded_parent))
    assert _ratings(snap)["mixed_doubles"]["inherited_live_updates"] == 2
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def test_a_zero_count_state_keeps_its_own_source_date() -> None:
    # Derived from an unplayed seeded parent, which is then reseeded. The
    # reseed refreshes the derived state (issue #101), but a snapshot made
    # before that fix holds the old date. Such a state keeps its own date,
    # and its count is 0.
    engine = RatingEngine()
    first = ExternalRatingSnapshot("external_standard", 4.0, date(2025, 1, 1))
    engine.add_player(derived_seed("syn-a", first, MX))
    second = ExternalRatingSnapshot("external_standard", 4.1, date(2025, 1, 5))
    engine.seed_external_rating("syn-a", D, second, replace_unplayed_seed=True)
    snap = _json(engine)
    assert _ratings(snap)["mixed_doubles"]["initial_source_date"] == "2025-01-05"
    _ratings(snap)["mixed_doubles"].update(
        rating=4.0, initial_rating=4.0, rd_as_of="2025-01-01", initial_source_date="2025-01-01"
    )
    assert _ratings(snap)["doubles"]["initial_source_date"] == "2025-01-05"
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def test_a_count_with_no_parent_state_names_the_missing_parent() -> None:
    snap = _json(_live_engine())
    del _ratings(snap)["doubles"]
    with pytest.raises(
        SnapshotError,
        match=r"mixed_doubles\.inherited_live_updates needs a doubles state, which is missing",
    ):
        RatingEngine.from_snapshot(snap)


# Small items


def _replayed() -> RatingEngine:
    seed = Player("syn-a", external_ids={"src": "ext-a"}, account_user_id="acct-a")
    return replay(HISTORY, [seed], as_of=CUT)


EMPTY: dict[str, tuple[Callable[[RatingEngine], object], str]] = {
    "empty account": (
        lambda e: e.link_account("syn-a", ""),
        "account_user_id is required",
    ),
    "empty source": (
        lambda e: e.attach_external_id("syn-a", "", "ext-q"),
        "source and external_player_id are required",
    ),
    "empty external id": (
        lambda e: e.attach_external_id("syn-a", "src", ""),
        "source and external_player_id are required",
    ),
}


@pytest.mark.parametrize(("call", "message"), list(EMPTY.values()), ids=list(EMPTY))
def test_an_empty_field_on_an_existing_player_raises_and_changes_nothing(
    call: Callable[[RatingEngine], object], message: str
) -> None:
    engine = _replayed()
    before = json.dumps(engine.to_snapshot(), sort_keys=True)
    with pytest.raises(PlayerError, match=f"^{message}$"):
        call(engine)
    assert json.dumps(engine.to_snapshot(), sort_keys=True) == before


def test_a_prequential_seed_with_an_inherited_count_is_rejected() -> None:
    state = RatingState(rating=3.5, rd=Config().new_player_rd)
    seed = Player("syn-z", {MX: dataclasses.replace(state, inherited_live_updates=1)})
    with pytest.raises(InvalidSeedError, match="inherited_live_updates must be 0"):
        prequential_predictions(HISTORY, [seed])
