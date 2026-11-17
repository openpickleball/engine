"""A seed's external_ids and account_user_id of the wrong type raise InvalidSeedError."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from open_engine import (
    InvalidSeedError,
    Player,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)

ROWS: list[tuple[str, dict[str, Any], str]] = [
    ("external_ids_none", {"external_ids": None}, "external_ids must be a dict"),
    ("external_ids_list", {"external_ids": [1]}, "external_ids must be a dict"),
    ("external_ids_int_key", {"external_ids": {1: "x"}}, "external_ids must map str to str"),
    ("external_ids_int_value", {"external_ids": {"src": 1}}, "external_ids must map str to str"),
    ("account_int", {"account_user_id": 5}, "account_user_id must be a str or None"),
    ("account_bytes", {"account_user_id": b"a"}, "account_user_id must be a str or None"),
]
IDS = [row[0] for row in ROWS]
PARAMS = [row[1:] for row in ROWS]


def _seed(fields: dict[str, Any]) -> Player:
    return Player("h", {D: RatingEngine().seed_provisional_rating("h", D)}, **fields)


@pytest.mark.parametrize(("fields", "message"), PARAMS, ids=IDS)
def test_add_player(fields: dict[str, Any], message: str) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=f"Seed for h: {message}"):
        eng.add_player(_seed(fields))
    assert eng.players == {}


@pytest.mark.parametrize(("fields", "message"), PARAMS, ids=IDS)
def test_replay(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(InvalidSeedError, match=f"Seed for h: {message}"):
        replay([], [_seed(fields)], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("fields", "message"), PARAMS, ids=IDS)
def test_prequential(fields: dict[str, Any], message: str, mode: Any) -> None:
    with pytest.raises(InvalidSeedError, match=f"Seed for h: {message}"):
        prequential_predictions([], [_seed(fields)], mode=mode)


def test_valid_identity_fields_are_accepted() -> None:
    seed = Player("h", {}, external_ids={"src": "ext-1"}, account_user_id="acct-1")
    engine = replay([], [seed], as_of=AS_OF)
    assert engine.players["h"].external_ids == {"src": "ext-1"}
    assert engine.players["h"].account_user_id == "acct-1"
