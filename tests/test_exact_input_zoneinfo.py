"""An exact zoneinfo.ZoneInfo is accepted as played_at's tzinfo (user decision).

An exact ZoneInfo is standard library code with no instance __dict__, so
no caller code runs when the engine reads its offset, and its offsets
come from the tz database. A ZoneInfo subclass can override utcoffset,
so it is still rejected before it runs. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import pytest

from open_engine import (
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
    ledger_order,
    prequential_predictions,
    replay,
)

zoneinfo = pytest.importorskip("zoneinfo")
ZoneInfo = zoneinfo.ZoneInfo

D = RatingFormat.DOUBLES
LATER = date(2026, 12, 1)


def _new_york() -> object:
    try:
        return ZoneInfo("America/New_York")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("tzdata for America/New_York is not installed")


def _ledger(convert: bool) -> list[Match]:
    """Rows across the New York fall-back fold, as ZoneInfo or fixed offsets."""
    zone = _new_york()
    rows = []
    for i in range(8):
        day = date(2026, 10, 30) + timedelta(days=i)
        at = datetime(day.year, day.month, day.day, 1, 30, tzinfo=zone, fold=i % 2)  # type: ignore[arg-type]
        if convert:
            at = at.astimezone(timezone(at.utcoffset()))  # type: ignore[arg-type]
        teams = (
            (("syn-a", "syn-b"), ("syn-c", "syn-d"))
            if i % 2
            else (("syn-a", "syn-c"), ("syn-b", "syn-d"))
        )
        rows.append(Match(f"syn-z{i}", *teams, ((11, 4 + i % 5),), day, D, played_at=at))
    return rows


def test_exact_zoneinfo_is_accepted() -> None:
    engine = RatingEngine()
    for row in _ledger(convert=False):
        Match.validate(row)
        ledger_order(row)
        assert engine.record(row).counted


def test_zoneinfo_replay_equals_the_fixed_offset_replay() -> None:
    zoned = replay(_ledger(convert=False), as_of=LATER).to_snapshot()
    fixed = replay(_ledger(convert=True), as_of=LATER).to_snapshot()
    assert json.dumps(zoned, sort_keys=True) == json.dumps(fixed, sort_keys=True)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_zoneinfo_prequential_equals_the_fixed_offset_prequential(mode: str) -> None:
    zoned = prequential_predictions(_ledger(convert=False), burn_in_matches=0, mode=mode)  # type: ignore[arg-type]
    fixed = prequential_predictions(_ledger(convert=True), burn_in_matches=0, mode=mode)  # type: ignore[arg-type]
    assert zoned == fixed
    assert zoned


class _RewritingZone(ZoneInfo):  # type: ignore[misc, valid-type]
    calls = 0
    target: Match | None = None

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        _RewritingZone.calls += 1
        if _RewritingZone.target is not None:
            object.__setattr__(_RewritingZone.target, "match_id", "")
        return super().utcoffset(dt)  # type: ignore[no-any-return]


ENTRY_POINTS: list[tuple[str, Callable[[RatingEngine, Match], object]]] = [
    ("validate", lambda engine, match: Match.validate(match)),
    ("ledger_order", lambda engine, match: ledger_order(match)),
    ("record", lambda engine, match: engine.record(match)),
    ("replay", lambda engine, match: replay([match], as_of=LATER)),
    ("prequential live", lambda engine, match: prequential_predictions([match])),
    (
        "prequential scheduled",
        lambda engine, match: prequential_predictions([match], mode="scheduled"),
    ),
]


@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_zoneinfo_subclass_callback_never_runs(
    name: str, call: Callable[[RatingEngine, Match], object]
) -> None:
    _new_york()
    zone = _RewritingZone("America/New_York")
    match = Match(
        "syn-m1",
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, 7),),
        date(2026, 3, 1),
        D,
        played_at=datetime(2026, 3, 1, 10, tzinfo=zone),
    )
    _RewritingZone.target = match
    _RewritingZone.calls = 0
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="played_at tzinfo must be"):
        call(engine, match)
    assert _RewritingZone.calls == 0
    assert match.match_id == "syn-m1"
    assert engine.processed_match_ids == set()
    assert engine.to_snapshot() == before
    _RewritingZone.target = None
