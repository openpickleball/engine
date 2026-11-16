"""A snapshot accepts every identity the engine's own methods produce (issue #16).

Identity values do not affect ratings, and the engine lets seeds and its
link methods set shared or blank account and external ids. A snapshot of
such an engine must still save and load. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import Player, RatingEngine, replay

AS_OF = date(2025, 1, 1)


def _shared_account() -> RatingEngine:
    seeds = [
        Player(id="syn-a1", account_user_id="acct-1"),
        Player(id="syn-a2", account_user_id="acct-1"),
    ]
    return replay([], seeds, as_of=AS_OF)


def _shared_external_id() -> RatingEngine:
    seeds = [
        Player(id="syn-b1", external_ids={"src": "x1"}),
        Player(id="syn-b2", external_ids={"src": "x1"}),
    ]
    return replay([], seeds, as_of=AS_OF)


def _blank_seed_ids() -> RatingEngine:
    seed = Player(id="syn-c1", external_ids={"src": " "}, account_user_id=" ")
    return replay([], [seed], as_of=AS_OF)


def _blank_linked_account() -> RatingEngine:
    engine = RatingEngine()
    engine.link_account("syn-d1", "   ")
    return engine


def _blank_attached_source() -> RatingEngine:
    engine = RatingEngine()
    engine.attach_external_id("syn-d1", " ", "id")
    return engine


BUILDERS: dict[str, Callable[[], RatingEngine]] = {
    "A shared account from seeds": _shared_account,
    "A2 shared external id from seeds": _shared_external_id,
    "A3 blank ids on a seed": _blank_seed_ids,
    "B blank account from link_account": _blank_linked_account,
    "B2 blank source from attach_external_id": _blank_attached_source,
}


@pytest.mark.parametrize("build", list(BUILDERS.values()), ids=list(BUILDERS))
def test_api_built_identities_round_trip(build: Callable[[], RatingEngine]) -> None:
    engine = build()
    first = json.loads(json.dumps(engine.to_snapshot()))
    restored = RatingEngine.from_snapshot(first)
    assert restored.players == engine.players
    assert restored.to_snapshot() == first
