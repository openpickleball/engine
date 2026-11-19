"""Evaluation rejects a score_from or anchor that is not a plain date."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest

from open_engine import (
    EvaluationError,
    Match,
    RatingFormat,
    evaluate,
    fit_scale,
    fit_win_calibration,
    prequential_predictions,
)

D = RatingFormat.DOUBLES
M1 = Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 3, 2), D)
BAD_ROW = Match("", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 3, 2), D)


class _Day(date):
    """A date subclass that is not a datetime."""


BAD_DATES = {
    # A subclass can override comparison, so since issue #119 only an
    # exact date is accepted.
    "date subclass": _Day(2026, 3, 1),
    "str": "2026-03-01",
    "naive datetime": datetime(2026, 3, 1),
    "aware datetime": datetime(2026, 3, 1, tzinfo=UTC),
    "int": 20260301,
}
LEDGERS = {"empty": [], "one row": [M1]}
FUNCTIONS: dict[str, Callable[..., Any]] = {
    "prequential_predictions": prequential_predictions,
    "evaluate": evaluate,
    "fit_scale": fit_scale,
    "fit_win_calibration": fit_win_calibration,
}


@pytest.mark.parametrize("func", list(FUNCTIONS.values()), ids=list(FUNCTIONS))
@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("ledger", list(LEDGERS.values()), ids=list(LEDGERS))
@pytest.mark.parametrize("value", list(BAD_DATES.values()), ids=list(BAD_DATES))
@pytest.mark.parametrize("argument", ["score_from", "anchor"])
def test_bad_date_raises(
    func: Callable[..., Any], mode: str, ledger: list[Match], value: object, argument: str
) -> None:
    with pytest.raises(EvaluationError, match=f"^{argument} must be a plain date"):
        func(ledger, mode=mode, **{argument: value})


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("argument", ["score_from", "anchor"])
def test_bad_date_raises_before_the_ledger_is_read(argument: str, mode: str) -> None:
    with pytest.raises(EvaluationError, match=f"^{argument} must be a plain date"):
        prequential_predictions([BAD_ROW], mode=mode, **{argument: datetime(2026, 3, 1)})


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("value", [None, date(2026, 3, 1)])
def test_plain_dates_and_none_are_accepted(mode: str, value: date | None) -> None:
    expected = prequential_predictions([M1], mode=mode)
    assert prequential_predictions([M1], mode=mode, score_from=value, anchor=value) == expected
