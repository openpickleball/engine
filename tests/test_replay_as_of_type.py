"""replay rejects an as_of that is not a plain date before anything else."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from open_engine import Match, RatingFormat, ReferenceDateError, replay

D = RatingFormat.DOUBLES
M1 = Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 3, 2), D)
M2 = Match("syn-m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 8),), date(2026, 4, 2), D)
BAD_ROW = Match("", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 3, 2), D)
BAD_AS_OF = {
    "str": "2026-03-31",
    "naive datetime": datetime(2026, 3, 31),
    "aware datetime": datetime(2026, 3, 31, tzinfo=UTC),
    "None": None,
    "int": 20260331,
}
LEDGERS = {"empty": [], "one row": [M1]}


class _Day(date):
    """A date subclass that is not a datetime."""


@pytest.mark.parametrize("ledger", list(LEDGERS.values()), ids=list(LEDGERS))
@pytest.mark.parametrize("as_of", list(BAD_AS_OF.values()), ids=list(BAD_AS_OF))
def test_bad_as_of_raises(as_of: object, ledger: list[Match]) -> None:
    with pytest.raises(ReferenceDateError, match="as_of"):
        replay(ledger, as_of=as_of)  # type: ignore[arg-type]


@pytest.mark.parametrize("as_of", list(BAD_AS_OF.values()), ids=list(BAD_AS_OF))
def test_bad_as_of_raises_before_resume(as_of: object) -> None:
    snapshot = replay([M1], as_of=date(2026, 3, 31)).to_snapshot()
    with pytest.raises(ReferenceDateError, match="as_of"):
        replay([M1, M2], as_of=as_of, from_snapshot=snapshot)  # type: ignore[arg-type]


@pytest.mark.parametrize("as_of", list(BAD_AS_OF.values()), ids=list(BAD_AS_OF))
def test_bad_as_of_raises_before_row_checks(as_of: object) -> None:
    with pytest.raises(ReferenceDateError, match="as_of"):
        replay([BAD_ROW], as_of=as_of)  # type: ignore[arg-type]


@pytest.mark.parametrize("whole_ledger", [True, 1], ids=["True", "int"])
def test_bad_as_of_raises_before_whole_ledger_checks(whole_ledger: object) -> None:
    with pytest.raises(ReferenceDateError, match="as_of"):
        replay([M1], as_of=datetime(2026, 3, 31), whole_ledger=whole_ledger)  # type: ignore[arg-type]


def test_date_subclass_as_of_is_accepted() -> None:
    plain = replay([M1], as_of=date(2026, 3, 31))
    sub = replay([M1], as_of=_Day(2026, 3, 31))
    assert sub.calculated_as_of == plain.calculated_as_of
    assert sub.to_snapshot()["players"] == plain.to_snapshot()["players"]
