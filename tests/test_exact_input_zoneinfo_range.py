"""An exact ZoneInfo whose tz data gives an offset datetime cannot hold is rejected.

ZoneInfo.from_file accepts TZif data with a UTC offset of a day or more,
and datetime then raises its own ValueError from utcoffset. The engine
raises InvalidMatchError instead, before any state change. The TZif bytes
are synthetic and built in memory.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def _tzif(offset_seconds: int, footer: bytes = b"") -> bytes:
    """Version 2 TZif data with one local time type and an optional POSIX footer."""

    def block() -> bytes:
        header = b"TZif2" + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, 0, 1, 4)
        return header + struct.pack(">lBB", offset_seconds, 0, 0) + b"XXX\0"

    return block() + block() + b"\n" + footer + b"\n"


ZONES: list[tuple[str, Callable[[], ZoneInfo]]] = [
    ("offset one day", lambda: ZoneInfo.from_file(io.BytesIO(_tzif(86400)), key="syn-zone")),
    ("offset over a day", lambda: ZoneInfo.from_file(io.BytesIO(_tzif(90000)), key="syn-zone")),
    (
        "negative offset over a day",
        lambda: ZoneInfo.from_file(io.BytesIO(_tzif(-90000)), key="syn-zone"),
    ),
    (
        "footer offset one day",
        lambda: ZoneInfo.from_file(io.BytesIO(_tzif(0, b"XXX-24")), key="syn-zone"),
    ),
]

ENTRY_POINTS: list[tuple[str, Callable[[RatingEngine, Match], object]]] = [
    ("validate", lambda engine, match: Match.validate(match)),
    ("ledger_order", lambda engine, match: ledger_order(match)),
    ("record", lambda engine, match: engine.record(match)),
    ("replay", lambda engine, match: replay([match], as_of=date(2027, 1, 1))),
    ("prequential live", lambda engine, match: prequential_predictions([match])),
    (
        "prequential scheduled",
        lambda engine, match: prequential_predictions([match], mode="scheduled"),
    ),
]


@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
@pytest.mark.parametrize(("case", "make"), ZONES, ids=[case for case, _ in ZONES])
def test_offset_the_zone_cannot_give_raises_invalid_match_error(
    case: str,
    make: Callable[[], ZoneInfo],
    name: str,
    call: Callable[[RatingEngine, Match], object],
) -> None:
    zone = make()
    at = datetime(2026, 7, 1, 12, tzinfo=zone)
    match = Match(
        "syn-m1",
        ("syn-a",),
        ("syn-b",),
        ((11, 5),),
        date(2026, 7, 1),
        RatingFormat.SINGLES,
        played_at=at,
    )
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="played_at"):
        call(engine, match)
    assert engine.to_snapshot() == before
