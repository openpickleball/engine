"""More coverage for GameFormat type checks (issue #88). All ids are synthetic."""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from typing import Any

import pytest

from open_engine import (
    ConfigError,
    GameFormat,
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
)

ON = date(2026, 1, 5)
BASE = Match("syn-m1", ("syn-a",), ("syn-b",), ((11, 5),), ON, RatingFormat.SINGLES)


class _Int(int):
    """An int subclass, rejected since issue #116: it could override comparison."""


def _engine() -> RatingEngine:
    engine = RatingEngine()
    warm_up = Match("syn-m0", ("syn-a",), ("syn-c",), ((11, 7),), ON, RatingFormat.SINGLES)
    assert engine.record(warm_up).counted
    return engine


@pytest.mark.parametrize(
    ("plain", "subclass"),
    [
        pytest.param({"points_to": 11}, {"points_to": _Int(11)}, id="points_to"),
        pytest.param({"win_by": 2}, {"win_by": _Int(2)}, id="win_by"),
        pytest.param({"cap": 15}, {"cap": _Int(15)}, id="cap"),
    ],
)
def test_an_int_subclass_is_rejected_where_the_int_rates(
    plain: dict[str, Any], subclass: dict[str, Any]
):
    engine = _engine()
    fmt = GameFormat(**plain)
    engine.predict_win_probability(("syn-a",), ("syn-b",), RatingFormat.SINGLES, fmt, 3)
    assert engine.record(dataclasses.replace(BASE, game_format=fmt)).counted

    engine = _engine()
    before = engine.to_snapshot()
    bad = GameFormat(**subclass)
    name = next(iter(subclass))
    with pytest.raises(ConfigError, match=f"^{name} must be an int.*, not _Int$"):
        engine.predict_win_probability(("syn-a",), ("syn-b",), RatingFormat.SINGLES, bad, 3)
    with pytest.raises(InvalidMatchError, match=f"^{name} must be an int.*, not _Int$"):
        engine.record(dataclasses.replace(BASE, game_format=bad))
    assert engine.to_snapshot() == before


def _valid(i: int) -> Match:
    return Match(
        f"syn-v{i}",
        ("syn-p",),
        ("syn-q",),
        ((11, i % 9),),
        ON + timedelta(days=i),
        RatingFormat.SINGLES,
    )


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("position", ["first", "last"])
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"points_to": 11.0}, "points_to must be an int, not float", id="type"),
        pytest.param({"scoring": "rally"}, "scoring must be a ScoringType, not str", id="scoring"),
        pytest.param({"cap": 5}, "cap must be greater than points_to", id="range"),
    ],
)
def test_prequential_rejects_a_bad_game_format_first_or_last(
    changes: dict[str, Any], message: str, position: str, mode: Any
):
    bad = dataclasses.replace(BASE, game_format=GameFormat(**changes))
    rows = [_valid(i) for i in range(6)]
    ledger = [bad, *rows] if position == "first" else [*rows, bad]
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        prequential_predictions(ledger, burn_in_matches=0, mode=mode)
