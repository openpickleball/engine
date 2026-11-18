"""A snapshot loads shared or blank account and external ids (issues #16, #52).

Identity values do not affect ratings. Since issue #52, no engine method
builds a shared or blank identity, but snapshot loading still only type
checks them until issue #61 makes it match the entry points. These tests
set the identities directly on the engine's players. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import Player, RatingEngine, replay

AS_OF = date(2025, 1, 1)


def _with_players(*players: Player) -> RatingEngine:
    engine = replay([], [], as_of=AS_OF)
    for player in players:
        engine.players[player.id] = player
    return engine


def _shared_account() -> RatingEngine:
    return _with_players(
        Player(id="syn-a1", account_user_id="acct-1"),
        Player(id="syn-a2", account_user_id="acct-1"),
    )


def _shared_external_id() -> RatingEngine:
    return _with_players(
        Player(id="syn-b1", external_ids={"src": "x1"}),
        Player(id="syn-b2", external_ids={"src": "x1"}),
    )


def _blank_ids() -> RatingEngine:
    return _with_players(Player(id="syn-c1", external_ids={" ": " "}, account_user_id=" "))


BUILDERS: dict[str, Callable[[], RatingEngine]] = {
    "A shared account": _shared_account,
    "A2 shared external id": _shared_external_id,
    "A3 blank account, source, and external id": _blank_ids,
}


@pytest.mark.parametrize("build", list(BUILDERS.values()), ids=list(BUILDERS))
def test_set_identities_round_trip(build: Callable[[], RatingEngine]) -> None:
    engine = build()
    first = json.loads(json.dumps(engine.to_snapshot()))
    restored = RatingEngine.from_snapshot(first)
    assert restored.players == engine.players
    assert restored.to_snapshot() == first
