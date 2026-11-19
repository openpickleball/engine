"""A GameFormat subclass that overrides validate is rejected (issue #116).

Before issue #116, Match.validate ran a subclass's own validate (issue #88
review). A subclass can also override a field with a property that runs
caller code, so a match now needs exactly a GameFormat, and the override
never runs. All ids are synthetic.
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

    calls = 0

    def validate(self) -> None:
        _StrictFormat.calls += 1
        raise ConfigError("syn rule")


SUBCLASS = "^game_format must be a GameFormat, not _StrictFormat$"


def test_match_validate_rejects_the_subclass_before_its_validate():
    row = dataclasses.replace(BASE, game_format=_StrictFormat())
    calls = _StrictFormat.calls
    with pytest.raises(InvalidMatchError, match=SUBCLASS):
        row.validate()
    assert _StrictFormat.calls == calls


def test_record_rejects_the_subclass_and_changes_nothing():
    engine = RatingEngine()
    other = Match("syn-m0", ("syn-f",), ("syn-g",), ((11, 3),), ON, RatingFormat.SINGLES)
    assert engine.record(other).counted
    before = engine.to_snapshot()
    calls = _StrictFormat.calls
    with pytest.raises(InvalidMatchError, match=SUBCLASS):
        engine.record(dataclasses.replace(BASE, game_format=_StrictFormat()))
    assert engine.to_snapshot() == before
    assert not {"syn-a", "syn-b"} & set(engine.players)
    assert _StrictFormat.calls == calls
