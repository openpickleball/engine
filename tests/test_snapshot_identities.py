"""A snapshot holds only identities the entry points accept (issues #16, #52, #61).

Since issue #52, no engine method builds a shared or blank account or
external id, so from_snapshot rejects them too. Distinct, non-blank
identities made by the engine's own methods round trip. All ids are
synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Player,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

AS_OF = date(2025, 1, 1)
S = RatingFormat.SINGLES

Snap = dict[str, object]


def _snapshot(*ids: str) -> Snap:
    engine = replay([], [Player(id=pid) for pid in ids], as_of=AS_OF)
    loaded: Snap = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _player(snap: Snap, index: int) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    player: dict[str, object] = players[index]
    return player


def _shared_account(snap: Snap) -> None:
    _player(snap, 0)["account_user_id"] = "acct-1"
    _player(snap, 1)["account_user_id"] = "acct-1"


def _shared_external_id(snap: Snap) -> None:
    _player(snap, 0)["external_ids"] = {"src": "x1"}
    _player(snap, 1)["external_ids"] = {"src": "x1"}


def _blank_account(snap: Snap) -> None:
    _player(snap, 1)["account_user_id"] = " "


def _empty_account(snap: Snap) -> None:
    _player(snap, 1)["account_user_id"] = ""


def _blank_source(snap: Snap) -> None:
    _player(snap, 1)["external_ids"] = {" ": "x1"}


def _blank_external_id(snap: Snap) -> None:
    _player(snap, 1)["external_ids"] = {"src": "\t"}


REJECTED: list[tuple[str, Callable[[Snap], None], str]] = [
    (
        "shared account",
        _shared_account,
        r"^snapshot\.players\[1\]\.account_user_id is held by another player$",
    ),
    (
        "shared external id",
        _shared_external_id,
        r"^snapshot\.players\[1\]\.external_ids\['src'\] is held by another player$",
    ),
    (
        "blank account",
        _blank_account,
        r"^snapshot\.players\[1\]\.account_user_id must be null or a non-blank str$",
    ),
    (
        "empty account",
        _empty_account,
        r"^snapshot\.players\[1\]\.account_user_id must be null or a non-blank str$",
    ),
    (
        "blank source",
        _blank_source,
        r"^snapshot\.players\[1\]\.external_ids must map non-blank str to non-blank str$",
    ),
    (
        "blank external id",
        _blank_external_id,
        r"^snapshot\.players\[1\]\.external_ids must map non-blank str to non-blank str$",
    ),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(m, msg, id=name) for name, m, msg in REJECTED],
)
def test_from_snapshot_rejects_a_blank_or_shared_identity(
    mutate: Callable[[Snap], None], message: str
) -> None:
    snapshot = _snapshot("syn-a1", "syn-a2")
    mutate(snapshot)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot)


def test_the_same_external_id_from_two_sources_loads() -> None:
    snapshot = _snapshot("syn-a1", "syn-a2")
    _player(snapshot, 0)["external_ids"] = {"src-1": "x1"}
    _player(snapshot, 1)["external_ids"] = {"src-2": "x1"}
    assert RatingEngine.from_snapshot(snapshot).to_snapshot() == snapshot


def test_distinct_identities_made_by_the_engine_round_trip() -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-b1", S)
    engine.link_account("syn-b1", "acct-1")
    engine.seed_provisional_rating("syn-b2", S)
    engine.attach_external_id("syn-b2", "src", "x2")
    engine.seed_external_rating(
        "syn-b3",
        S,
        ExternalRatingSnapshot("src", 4.0, date(2025, 1, 1), 60.0, external_player_id="x3"),
    )
    engine.add_player(Player(id="syn-b4", external_ids={"src-2": "x4"}, account_user_id="acct-4"))
    first = json.loads(json.dumps(engine.to_snapshot()))
    restored = RatingEngine.from_snapshot(first)
    assert restored.players == engine.players
    assert restored.to_snapshot() == first
