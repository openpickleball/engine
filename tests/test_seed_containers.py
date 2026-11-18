"""A seed that is not a Player, or holds the wrong containers, raises InvalidSeedError."""

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


def _player(pid: Any, ratings: Any) -> Any:
    return Player(pid, ratings)


ROWS: list[tuple[str, Any, str]] = [
    ("not_a_player", None, r"Seed must be a Player, not NoneType"),
    ("dict_seed", {"id": "h"}, r"Seed must be a Player, not dict"),
    ("list_id", _player(["h"], {}), r"Seed player id must be a str, not list"),
    ("int_id", _player(5, {}), r"Seed player id must be a str, not int"),
    ("ratings_none", _player("h", None), r"Seed for h: ratings must be a dict"),
    ("ratings_list", _player("h", []), r"Seed for h: ratings must be a dict"),
    (
        "state_none",
        _player("h", {D: None}),
        r"Seed for h in doubles: rating state must be a RatingState",
    ),
    (
        "state_dict",
        _player("h", {D: {"rating": 3.5}}),
        r"Seed for h in doubles: rating state must be a RatingState",
    ),
]
IDS = [row[0] for row in ROWS]
PARAMS = [row[1:] for row in ROWS]


@pytest.mark.parametrize(("seed", "message"), PARAMS, ids=IDS)
def test_add_player(seed: Any, message: str) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=message):
        eng.add_player(seed)
    assert eng.players == {}


@pytest.mark.parametrize(("seed", "message"), PARAMS, ids=IDS)
def test_replay(seed: Any, message: str) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        replay([], [seed], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("seed", "message"), PARAMS, ids=IDS)
def test_prequential(seed: Any, message: str, mode: Any) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        prequential_predictions([], [seed], mode=mode)


def test_blank_id_is_rejected() -> None:
    # Accepted before issue #52; tests/test_seed_identity_values.py covers every entry point.
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=r"^Seed player id must not be blank$"):
        eng.add_player(Player("", {}))
    assert eng.players == {}
