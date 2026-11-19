"""Caller code cannot run during match validation and rewrite the match (#109 route).

A tzinfo subclass's utcoffset, or a GameFormat subclass's field property,
would run while the match is checked and could rewrite another field of
the owning Match after its check passed. The exact type checks reject both
before either runs. All ids are synthetic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, tzinfo

import pytest

from open_engine import (
    GameFormat,
    InvalidMatchError,
    LedgerConflictError,
    Match,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
DAY = date(2026, 3, 1)
LATER = date(2026, 6, 1)

# Field name and the value the callback writes into the owning Match.
REWRITES: list[tuple[str, object]] = [
    ("match_id", ""),
    ("games", ((0, 11),)),
    ("confirmed_by", frozenset({"syn-x"})),
]


class _RewritingZone(tzinfo):
    def __init__(self, field: str, value: object) -> None:
        self.calls = 0
        self.field = field
        self.value = value
        self.target: Match | None = None

    def utcoffset(self, dt: datetime | None) -> timedelta:
        self.calls += 1
        if self.target is not None:
            object.__setattr__(self.target, self.field, self.value)
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "Z"


class _Callback:
    def __init__(self, field: str, value: object) -> None:
        self.calls = 0
        self.field = field
        self.value = value
        self.target: Match | None = None


class _RewritingFormat(GameFormat):
    @property  # type: ignore[override]
    def points_to(self) -> int:
        callback: _Callback = object.__getattribute__(self, "callback")
        callback.calls += 1
        if callback.target is not None:
            object.__setattr__(callback.target, callback.field, callback.value)
        return 11

    @points_to.setter
    def points_to(self, value: int) -> None:
        pass


def _zone_match(field: str, value: object) -> tuple[Match, _RewritingZone]:
    zone = _RewritingZone(field, value)
    match = Match(
        "syn-m1",
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, 7),),
        DAY,
        D,
        played_at=datetime(2026, 3, 1, 10, tzinfo=zone),
    )
    zone.target = match
    return match, zone


def _format_match(field: str, value: object) -> tuple[Match, _Callback]:
    callback = _Callback(field, value)
    fmt = _RewritingFormat()
    object.__setattr__(fmt, "callback", callback)
    match = Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), DAY, D)
    object.__setattr__(match, "game_format", fmt)
    callback.target = match
    return match, callback


def _fields(match: Match) -> tuple[object, ...]:
    return (match.match_id, match.games, match.confirmed_by, match.team_a, match.team_b)


ENTRY_POINTS: list[tuple[str, Callable[[RatingEngine, Match], object]]] = [
    ("validate", lambda engine, match: Match.validate(match)),
    ("record", lambda engine, match: engine.record(match)),
    ("replay", lambda engine, match: replay([match], as_of=LATER)),
    ("prequential live", lambda engine, match: prequential_predictions([match])),
    (
        "prequential scheduled",
        lambda engine, match: prequential_predictions([match], mode="scheduled"),
    ),
]


@pytest.mark.parametrize(("field", "value"), REWRITES, ids=[field for field, _ in REWRITES])
@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_tzinfo_callback_never_runs(
    name: str, call: Callable[[RatingEngine, Match], object], field: str, value: object
) -> None:
    match, zone = _zone_match(field, value)
    before = _fields(match)
    engine = RatingEngine()
    snapshot = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="played_at tzinfo must be"):
        call(engine, match)
    assert zone.calls == 0
    assert _fields(match) == before
    assert engine.processed_match_ids == set()
    assert engine.to_snapshot() == snapshot


@pytest.mark.parametrize(("field", "value"), REWRITES, ids=[field for field, _ in REWRITES])
@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_game_format_callback_never_runs(
    name: str, call: Callable[[RatingEngine, Match], object], field: str, value: object
) -> None:
    match, callback = _format_match(field, value)
    before = _fields(match)
    engine = RatingEngine()
    snapshot = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="game_format must be"):
        call(engine, match)
    assert callback.calls == 0
    assert _fields(match) == before
    assert engine.processed_match_ids == set()
    assert engine.to_snapshot() == snapshot


def _rewriting_ledger() -> Iterator[Match]:
    """Yield a row, then on the next step rewrite it to reuse the next row's id."""
    first = Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), DAY, D)
    second = Match(
        "syn-m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 3),), date(2026, 3, 2), D
    )
    yield first
    object.__setattr__(first, "match_id", "syn-m2")
    yield second


@pytest.mark.parametrize(
    "call",
    [
        lambda rows: replay(rows, as_of=LATER),
        lambda rows: prequential_predictions(rows),
        lambda rows: prequential_predictions(rows, mode="scheduled"),
    ],
    ids=["replay", "prequential live", "prequential scheduled"],
)
def test_generator_ledger_read_whole_before_validation(
    call: Callable[[Iterator[Match]], object],
) -> None:
    # Read whole first, both rows carry syn-m2 with different content when
    # validated, so the rated rows are the validated rows: a conflict.
    with pytest.raises(LedgerConflictError, match="syn-m2"):
        call(_rewriting_ledger())
