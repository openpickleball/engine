"""Match.validate runs a GameFormat's own validate (issue #88 review).

A GameFormat subclass may add rules by overriding validate. A ConfigError it
raises reaches a match as InvalidMatchError with the same message. All ids
are synthetic.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from open_engine import (
    ConfigError,
    GameFormat,
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
)

ON = date(2026, 1, 5)
BASE = Match("syn-m1", ("syn-a",), ("syn-b",), ((11, 5),), ON, RatingFormat.SINGLES)


@dataclasses.dataclass(frozen=True)
class _StrictFormat(GameFormat):
    """A synthetic format with one extra rule."""

    def validate(self) -> None:
        super().validate()
        raise ConfigError("syn rule")


def test_match_validate_raises_the_override_as_invalid_match_error():
    row = dataclasses.replace(BASE, game_format=_StrictFormat())
    with pytest.raises(InvalidMatchError, match="^syn rule$"):
        row.validate()


def test_record_rejects_the_override_and_changes_nothing():
    engine = RatingEngine()
    other = Match("syn-m0", ("syn-f",), ("syn-g",), ((11, 3),), ON, RatingFormat.SINGLES)
    assert engine.record(other).counted
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="^syn rule$"):
        engine.record(dataclasses.replace(BASE, game_format=_StrictFormat()))
    assert engine.to_snapshot() == before
    assert not {"syn-a", "syn-b"} & set(engine.players)
