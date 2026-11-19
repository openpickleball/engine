"""RD checks for an unplayed state with an inherited live count (issue #23 addendum).

Such a state comes only from deriving a category state from a played
parent, so its RD is at least category_seed_rd and its rd_as_of is set and
no later than the parent's. Once the category state has played, its RD
moves on its own. Since issue #112 the engine stores no such state, but
snapshots from an earlier release can hold one and must still load. All
ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from helpers import store_state
from open_engine import Match, RatingEngine, RatingFormat, SnapshotError, replay

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
CUT = date(2025, 3, 1)
TEAMS = (("syn-a", "syn-b"), ("syn-c", "syn-d"))
HISTORY = [Match("syn-h1", *TEAMS, ((11, 4),), date(2025, 2, 1), D)]


def _live_engine(play_category: bool = False) -> RatingEngine:
    engine = replay(HISTORY, as_of=CUT)
    for i in range(2):
        match = Match(f"syn-l{i}", *TEAMS, ((11, 4),), date(2025, 3, 2 + i), D)
        assert engine.record(match).counted
    if play_category:
        match = Match("syn-x1", *TEAMS, ((11, 6),), date(2025, 3, 5), MX)
        assert engine.record(match).counted
    else:
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


TAMPERED: list[tuple[str, dict[str, object], str]] = [
    (
        "external seed rd",
        {"rd": 0.42},
        r"mixed_doubles\.rd must be at least category_seed_rd",
    ),
    (
        "no rd_as_of",
        {"rd_as_of": None},
        r"mixed_doubles\.rd_as_of must be set",
    ),
    (
        "rd_as_of after the parent's",
        {"rd_as_of": "2025-03-20"},
        r"mixed_doubles\.rd_as_of must not be after the parent state's rd_as_of",
    ),
]


@pytest.mark.parametrize(
    ("changes", "message"), [case[1:] for case in TAMPERED], ids=[case[0] for case in TAMPERED]
)
def test_an_unplayed_inherited_state_with_a_foreign_rd_does_not_load(
    changes: dict[str, object], message: str
) -> None:
    snap = _json(_live_engine())
    state = _ratings(snap)["mixed_doubles"]
    assert state["inherited_live_updates"] == 2 and state["matches_played"] == 0
    state.update(changes)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap)


@pytest.mark.parametrize("play_category", [False, True], ids=["unplayed", "played"])
def test_an_engine_made_inherited_state_loads(play_category: bool) -> None:
    snap = _json(_live_engine(play_category))
    state = _ratings(snap)["mixed_doubles"]
    assert state["inherited_live_updates"] == 2
    assert (state["matches_played"] > 0) is play_category
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def test_a_played_inherited_state_may_have_any_valid_rd() -> None:
    snap = _json(_live_engine(play_category=True))
    state = _ratings(snap)["mixed_doubles"]
    state["rd"] = 0.3
    state["rd_as_of"] = "2025-03-05"
    restored = RatingEngine.from_snapshot(snap)
    assert restored.players["syn-a"].ratings[MX].rd == 0.3
