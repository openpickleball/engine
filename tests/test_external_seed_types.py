"""Every snapshot seed_external_rating accepts gives a state that replay accepts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    RatingEngine,
    RatingFormat,
    replay,
)

D = RatingFormat.DOUBLES
CFG = Config()
BASE = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 10, 1), 85)

REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("captured_datetime", {"captured_on": datetime(2026, 10, 1)}, "captured_on must be a date"),
    ("captured_str", {"captured_on": "2026-10-01"}, "captured_on must be a date"),
    ("rating_bool", {"rating": True}, "rating must be a finite number"),
    ("rating_str", {"rating": "4.0"}, "rating must be a finite number"),
    ("rating_nan", {"rating": float("nan")}, "rating must be a finite number"),
    ("rating_decimal", {"rating": Decimal("4")}, "rating must be a finite number"),
    ("reliability_bool", {"reliability": True}, "reliability must be a finite number"),
    ("reliability_str", {"reliability": "85"}, "reliability must be a finite number"),
    ("reliability_nan", {"reliability": float("nan")}, "reliability must be a finite number"),
    (
        "source_int",
        {"source": 5, "external_player_id": "ext-1"},
        "source must be a str",
    ),
    ("external_id_int", {"external_player_id": 5}, "external_player_id must be a str or None"),
]


@pytest.mark.parametrize(
    ("changes", "message"), [r[1:] for r in REJECTED], ids=[r[0] for r in REJECTED]
)
def test_snapshot_of_wrong_type_is_rejected_before_any_change(
    changes: dict[str, Any], message: str
) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match=message):
        eng.seed_external_rating("a", D, replace(BASE, **changes))
    assert eng.players == {}


ACCEPTED: list[tuple[str, dict[str, Any]]] = [
    ("base", {}),
    ("no_reliability", {"reliability": None}),
    ("int_rating", {"rating": 4}),
    ("float_reliability", {"reliability": 99.5}),
    ("reliability_bounds_low", {"reliability": 0}),
    ("reliability_bounds_high", {"reliability": 100}),
    ("rating_min", {"rating": CFG.min_rating}),
    ("rating_max", {"rating": CFG.max_rating}),
    ("external_id", {"external_player_id": "ext-1", "evidence_reference": "ev-1"}),
]


@pytest.mark.parametrize("changes", [r[1] for r in ACCEPTED], ids=[r[0] for r in ACCEPTED])
def test_accepted_snapshot_seeds_replay(changes: dict[str, Any]) -> None:
    snapshot = replace(BASE, **changes)
    eng = RatingEngine()
    eng.seed_external_rating("a", D, snapshot)
    eng.seed_provisional_rating("a", RatingFormat.MIXED_DOUBLES)
    seeded = eng.players["a"]
    RatingEngine().add_player(seeded)
    rebuilt = replay([], [seeded], as_of=snapshot.captured_on)
    assert rebuilt.players["a"].ratings[D].rating == snapshot.rating
