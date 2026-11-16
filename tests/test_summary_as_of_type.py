"""player_summary rejects an as_of that is not a plain date before it changes anything."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from open_engine import Match, RatingFormat, ReferenceDateError, replay

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
REPLAYED = date(2026, 3, 1)
LEDGER = [Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 2, 1), D)]
BAD_AS_OF = {
    "str": "2026-01-01",
    "datetime": datetime(2026, 1, 1, tzinfo=UTC),
    "naive datetime": datetime(2026, 1, 1),
}


@pytest.mark.parametrize("as_of", list(BAD_AS_OF.values()), ids=list(BAD_AS_OF))
def test_bad_as_of_raises_before_any_change(as_of: object) -> None:
    eng = replay(LEDGER, as_of=REPLAYED)
    with pytest.raises(ReferenceDateError, match="as_of"):
        eng.player_summary("syn-a", MX, as_of=as_of)  # type: ignore[arg-type]
    with pytest.raises(ReferenceDateError, match="as_of"):
        eng.player_summary("syn-new", D, as_of=as_of)  # type: ignore[arg-type]
    assert MX not in eng.players["syn-a"].ratings
    assert "syn-new" not in eng.players
    assert eng.to_snapshot()["resumable"] is True


@pytest.mark.parametrize("as_of", list(BAD_AS_OF.values()), ids=list(BAD_AS_OF))
def test_bad_as_of_raises_for_an_existing_state(as_of: object) -> None:
    eng = replay(LEDGER, as_of=REPLAYED)
    with pytest.raises(ReferenceDateError, match="as_of"):
        eng.player_summary("syn-a", D, as_of=as_of)  # type: ignore[arg-type]


def test_datetime_as_of_raises_for_an_unplayed_state() -> None:
    eng = replay(LEDGER, as_of=REPLAYED)
    eng.seed_provisional_rating("syn-u", D)
    with pytest.raises(ReferenceDateError, match="as_of"):
        eng.player_summary("syn-u", D, as_of=datetime(2026, 1, 1))  # type: ignore[arg-type]
